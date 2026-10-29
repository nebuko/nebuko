"""Capture seam for token telemetry (issue #829).

Adapts LiteLLM's per-call logging events into :class:`TokenTelemetry`
observations. This is the single instrumentation surface of the metrics
wire — the layer OpenHands already drives every completion through, so a
future observability sink (LangFuse, Prometheus, an OTEL exporter) attaches
alongside us rather than at a second, parallel seam.

The seam was pinned by a feasibility spike
(``test_capture_spike.py``) against the real SDK streaming path:

  * ``log_pre_api_call`` fires **synchronously** before the request is sent,
    so it is the request-start stamp.
  * the ``on_token`` callback (passed to ``Conversation(token_callbacks=...)``)
    fires per streamed chunk; the **final** chunk already carries ``usage`` —
    giving the first-token stamp, the finish stamp and the billed counts in
    one place.
  * ``log_success_event`` / ``async_log_success_event`` are dispatched as a
    fire-and-forget task on the loop and are *not* delivered by the time a
    streaming call returns, so they cannot be the sole terminal capture. They
    remain wired as a best-effort fallback for non-streamed calls.

Crucially, the SDK has no first-token timing of its own, so the streaming
``on_token`` callback must stay wired — its presence also keeps streaming on
for the ``stream_idle_timeout`` guard (issue #775).
"""

import os
import time
from dataclasses import dataclass
from typing import Any

import litellm
from litellm.integrations.custom_logger import CustomLogger

from nebuko.engine.lib.agent_script.sub_domains.metrics.token_telemetry import TokenTelemetry


@dataclass(frozen=True)
class CallStamps:
    """Immutable bracket for one in-flight LLM call.

    ``first_token_at`` starts unset and is filled by the first streamed chunk.
    """

    request_started_at: float
    first_token_at: float | None = None


def _as_int(value: Any) -> int:
    """Coerce a (possibly ``None``) usage count to an int."""
    return int(value or 0)


def _cache_tokens(usage: Any) -> tuple[int, int]:
    """Cache-read/cache-write token counts from a LiteLLM usage object.

    LiteLLM surfaces these on ``prompt_tokens_details`` (``cached_tokens`` /
    ``cache_write_tokens``); the private attributes are a fallback for older
    usage shapes.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    cache_read = getattr(details, "cached_tokens", None) if details else None
    cache_write = getattr(details, "cache_write_tokens", None) if details else None
    if cache_read is None:
        cache_read = getattr(usage, "_cache_read_input_tokens", None)
    if cache_write is None:
        cache_write = getattr(usage, "_cache_creation_input_tokens", None)
    return _as_int(cache_read), _as_int(cache_write)


def _call_id(kwargs: dict[str, Any]) -> str:
    """Identify an in-flight call by LiteLLM's per-call id."""
    return str(kwargs.get("litellm_call_id") or "")


def _observation(stamps: CallStamps, usage: Any, finished_at: float) -> TokenTelemetry:
    """Build the per-call observation from the bracket and the billed counts."""
    cache_read, cache_write = _cache_tokens(usage)
    return TokenTelemetry(
        request_started_at=stamps.request_started_at,
        first_token_at=stamps.first_token_at,
        request_finished_at=finished_at,
        prompt_tokens=_as_int(getattr(usage, "prompt_tokens", None)),
        completion_tokens=_as_int(getattr(usage, "completion_tokens", None)),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


class TokenTelemetryCollector(CustomLogger):
    """Emit one :class:`TokenTelemetry` per observed LLM call.

    Register with :func:`install` (which attaches it to ``litellm.callbacks``
    for the request-start stamp) and pass ``on_token`` as a conversation token
    callback (for the first-token stamp, finish stamp and usage).
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_flight: dict[str, CallStamps] = {}
        self._current_call_id: str | None = None
        self._observations: list[TokenTelemetry] = []

    # ── Request start ────────────────────────────────────────────

    def log_pre_api_call(self, model: Any, messages: Any, kwargs: dict[str, Any]) -> None:
        call_id = _call_id(kwargs)
        self._in_flight[call_id] = CallStamps(request_started_at=time.monotonic())
        self._current_call_id = call_id

    # ── Chunks (first token, finish, usage) ──────────────────────

    def on_token(self, chunk: Any) -> None:
        call_id = self._current_call_id
        stamps = self._in_flight.get(call_id) if call_id is not None else None
        if stamps is None:
            return
        now = time.monotonic()
        if stamps.first_token_at is None:
            stamps = CallStamps(stamps.request_started_at, now)
            self._in_flight[call_id] = stamps
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            self._close(call_id, stamps, usage, now)

    # ── Terminal (best-effort fallback for non-streamed calls) ───

    def log_success_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        call_id = _call_id(kwargs)
        stamps = self._in_flight.pop(call_id, None)
        if stamps is None:
            # Already closed from the streamed final chunk — do not double count.
            return
        if call_id == self._current_call_id:
            self._current_call_id = None
        self._observations.append(
            _observation(stamps, getattr(response_obj, "usage", None), time.monotonic())
        )

    def log_failure_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Drop a failed call — partial observations would skew the means."""
        call_id = _call_id(kwargs)
        self._in_flight.pop(call_id, None)
        if call_id == self._current_call_id:
            self._current_call_id = None

    # ── Read-out ─────────────────────────────────────────────────

    def drain(self) -> list[TokenTelemetry]:
        """Return the observations collected so far and clear them."""
        drained, self._observations = self._observations, []
        return drained

    def _close(self, call_id: str, stamps: CallStamps, usage: Any, finished_at: float) -> None:
        """Record a closed observation and release the in-flight bracket."""
        self._in_flight.pop(call_id, None)
        if call_id == self._current_call_id:
            self._current_call_id = None
        self._observations.append(_observation(stamps, usage, finished_at))


_active_collector: TokenTelemetryCollector | None = None


def install(collector: TokenTelemetryCollector) -> None:
    """Make ``collector`` the active capture sink and subscribe it to LiteLLM."""
    global _active_collector
    _active_collector = collector
    if collector not in litellm.callbacks:
        litellm.callbacks.append(collector)


def get_active_collector() -> TokenTelemetryCollector | None:
    """The collector wired for the current run, or ``None`` when none was installed."""
    return _active_collector


def maybe_register_langfuse() -> None:
    """Enable LangFuse tracing when — and only when — the env opts in.

    Default-off: nothing is registered and no telemetry leaves the machine
    until ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` are both set
    (``LANGFUSE_HOST`` selects a self-hosted instance). Registration is a
    no-op when the ``langfuse`` package is not installed, so opting in is
    purely a keys decision.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return
    if "langfuse" in litellm.success_callback:
        return
    try:
        import langfuse  # noqa: F401
    except ImportError:
        return
    litellm.success_callback = [*litellm.success_callback, "langfuse"]
