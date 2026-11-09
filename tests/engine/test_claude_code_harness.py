"""Claude Code harness tests (issue #1368).

The SDK's client is never started here: the session's async bridge is exercised
against a fake client, and the closing channel against the real ``FinalizeSink``.
The live smoke test is opt-in (``NEBUKO_LIVE_CLAUDE=1``) and skipped in CI.
"""

from __future__ import annotations

import os

import pytest

from nebuko.engine.finalize import TurnOutcomePayload
from nebuko.engine.harness.claude_code import (
    FINALIZE_TOOL,
    ClaudeCodeClosingChannel,
    ClaudeCodeSession,
)
from nebuko.engine.harness.engine_tools import FinalizeSink


class _FakeClient:
    """Stands in for ``ClaudeSDKClient``: records queries, replays messages."""

    def __init__(self, on_query=None) -> None:
        self.queries: list[str] = []
        self.connected = False
        self.disconnected = False
        self._on_query = on_query

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def query(self, text: str) -> None:
        self.queries.append(text)

    async def receive_response(self):
        if self._on_query is not None:
            self._on_query(self)
        if False:  # pragma: no cover - keeps this an async generator
            yield None


def _session(on_query=None) -> tuple[ClaudeCodeSession, _FakeClient, FinalizeSink]:
    sink = FinalizeSink()
    session = ClaudeCodeSession(options=None, sink=sink)
    client = _FakeClient(on_query)
    session._client = client
    return session, client, sink


def test_send_message_queries_the_client_across_turns() -> None:
    session, client, _ = _session()
    session.send_message("first")
    session.send_message("second")
    assert client.queries == ["first", "second"]


def test_tool_policy_denies_non_finalize_tools() -> None:
    session, _, _ = _session()
    assert session.permits("terminal")
    session.set_tool_policy(frozenset({FINALIZE_TOOL}))
    assert session.permits(FINALIZE_TOOL)
    assert not session.permits("terminal")
    session.set_tool_policy(None)
    assert session.permits("terminal")


def test_closing_channel_reads_the_sink() -> None:
    session, client, sink = _session()

    def _record(_client) -> None:
        sink.record(
            {"report": "## Report\n\n- via mcp", "turn_outcome": {"appreciation": "approved"}}
        )

    client._on_query = _record
    result = ClaudeCodeClosingChannel().elicit(session, None)

    assert result.report == "## Report\n\n- via mcp"
    assert result.outcome is not None
    assert result.outcome.appreciation == "approved"
    # The closing turn is the only turn that ran, and the policy was restored.
    assert client.queries == [client.queries[0]]
    assert session.permits("terminal")


def test_closing_channel_returns_empty_when_the_turn_records_nothing() -> None:
    session, _, _ = _session()
    result = ClaudeCodeClosingChannel().elicit(session, None)
    assert result.report == ""
    assert result.outcome is None


def test_session_reuses_the_client_across_turns() -> None:
    session, client, _ = _session()
    session.send_message("one")
    session.run()
    session.send_message("two")
    session.run()
    assert client.connected is False  # never connected: the fake is pre-bound
    assert client.queries == ["one", "two"]


@pytest.mark.skipif(
    os.environ.get("NEBUKO_LIVE_CLAUDE") != "1",
    reason="live Claude Code smoke test is opt-in (NEBUKO_LIVE_CLAUDE=1)",
)
def test_live_claude_finalize_tool() -> None:  # pragma: no cover - opt-in
    from nebuko.engine.harness import resolve_harness

    harness = resolve_harness("claude-code")
    built = harness.build_agent(
        role_name="dev", agent_cfg=None, prompt="You are a test agent.", agent_context=None
    )
    result = harness.make_closing_channel().elicit(built.session, None)
    assert isinstance(result.outcome, TurnOutcomePayload)
