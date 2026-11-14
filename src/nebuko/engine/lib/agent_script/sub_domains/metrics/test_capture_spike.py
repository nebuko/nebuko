"""Step 0 capture feasibility spike for the TPS/TTFT wire (issue #829).

Decides *where* the capture seam for the token telemetry must live. The
approved design (issue #829 plan) proposed a ``litellm.CustomLogger`` on
``litellm.callbacks`` and expected ``log_success_event`` to deliver each
call's terminal data synchronously. This test pins the measured reality:

  * ``log_pre_api_call`` fires **synchronously** on the OpenHands SDK path and
    is therefore a reliable request-start stamp.
  * ``on_token`` (``Conversation(token_callbacks=[...])``) fires per chunk and
    the **final** streamed chunk already carries ``usage`` — giving a reliable
    first-token stamp, finish stamp and billed counts.
  * the terminal ``log_success_event`` / ``async_log_success_event`` is
    scheduled as a fire-and-forget task on the run loop and is *not* delivered
    by the time the SDK call returns, so it cannot be the sole source.

The collector shape asserted here is the one the implementation must adopt.
"""

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

litellm = pytest.importorskip("litellm")
pytest.importorskip("openhands.sdk")

from litellm.integrations.custom_logger import CustomLogger

CHUNKS = ["Hello", " from", " the", " real", " server"]
PROMPT_TOKENS, COMPLETION_TOKENS = 7, 5


class _SSEHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible streaming endpoint (usage on the last chunk)."""

    def log_message(self, *args):  # silence the test output
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for text in CHUNKS:
            self._send({"choices": [{"index": 0, "delta": {"content": text},
                                     "finish_reason": None}]})
            time.sleep(0.01)
        self._send({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": PROMPT_TOKENS,
                              "completion_tokens": COMPLETION_TOKENS,
                              "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS}})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send(self, extra):
        chunk = {"id": "chatcmpl-spike", "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": "gpt-4o-mini"}
        chunk.update(extra)
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.flush()


@dataclass(frozen=True)
class _Observation:
    """One per-LLM-request observation (shape of the future TokenTelemetry)."""

    response_id: str | None
    request_started_at: float | None
    first_token_at: float | None
    request_finished_at: float
    prompt_tokens: int
    completion_tokens: int


class _Collector(CustomLogger):
    """Capture seam under test: request start from litellm, rest from chunks."""

    def __init__(self):
        self.observations: list[_Observation] = []
        self._started_at: float | None = None
        self._response_id: str | None = None
        self._first_token_at: float | None = None

    def log_pre_api_call(self, model, messages, kwargs):
        self._started_at = time.monotonic()
        self._response_id = None
        self._first_token_at = None

    def on_token(self, chunk):
        now = time.monotonic()
        if self._first_token_at is None:
            self._first_token_at = now
            self._response_id = getattr(chunk, "id", None)
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            self.observations.append(_Observation(
                response_id=self._response_id,
                request_started_at=self._started_at,
                first_token_at=self._first_token_at,
                request_finished_at=now,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ))


@pytest.fixture()
def sse_server():
    server = HTTPServer(("127.0.0.1", 0), _SSEHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def test_pre_api_call_and_on_token_yield_ttft_and_throughput(sse_server):
    """The SDK streaming path must yield usable request timing and usage."""
    import asyncio

    from openhands.sdk.llm import LLM, Message

    collector = _Collector()
    previous = litellm.callbacks
    litellm.callbacks = [*previous, collector]
    try:
        llm = LLM(model="openai/gpt-4o-mini", api_key="dummy",
                  base_url=sse_server, stream=True)
        asyncio.run(llm.acompletion(
            messages=[Message(role="user", content="hi")],
            on_token=collector.on_token,
        ))
    finally:
        litellm.callbacks = previous

    assert len(collector.observations) == 1, "expected exactly one closed observation"
    obs = collector.observations[0]

    # Request start is stamped by the synchronous litellm hook, before any chunk.
    assert obs.request_started_at is not None
    assert obs.first_token_at is not None
    assert obs.request_started_at <= obs.first_token_at <= obs.request_finished_at

    assert obs.prompt_tokens == PROMPT_TOKENS
    assert obs.completion_tokens == COMPLETION_TOKENS

    ttft = obs.first_token_at - obs.request_started_at
    span = obs.request_finished_at - obs.first_token_at
    assert ttft > 0, "time-to-first-token must be observable"
    assert span > 0, "generation span must be observable"
    assert obs.completion_tokens / span > 0, "generation throughput must be derivable"
