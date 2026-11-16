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
from nebuko.engine.harness.base import HarnessEvent


class _FakeClient:
    """Stands in for ``ClaudeSDKClient``: records queries, replays messages."""

    def __init__(self, on_query=None) -> None:
        self.queries: list[str] = []
        self.connected = False
        self.disconnected = False
        self.interrupted = False
        self._on_query = on_query

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def query(self, text: str) -> None:
        self.queries.append(text)

    async def interrupt(self) -> None:
        self.interrupted = True

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


def test_interrupt_stops_the_turn_but_keeps_the_session() -> None:
    session, client, _ = _session()
    session.send_message("one")  # binds the private loop the bridge submits on
    session.interrupt()
    assert client.interrupted is True
    assert client.disconnected is False
    assert session._client is client


def test_conversation_view_records_agent_text_and_carries_the_session() -> None:
    from nebuko.engine.harness.claude_code import ClaudeCodeConversation

    session, _, _ = _session()
    conversation = ClaudeCodeConversation(
        session=session,
        closing_channel=ClaudeCodeClosingChannel(),
        workspace="/tmp/repo",
        run_id="run-1",
    )
    session._emit(HarnessEvent(kind="commentary", text="work done"))

    assert conversation.id == "run-1"
    assert conversation.harness_session is session
    assert conversation.state.workspace.working_dir == "/tmp/repo"
    assert len(conversation.state.events) == 1


def test_declared_tools_map_to_claude_builtins_and_engine_mcp() -> None:
    from types import SimpleNamespace

    from nebuko.engine.harness.claude_code import _declared_tools

    cfg = SimpleNamespace(
        tools=[
            "terminal",
            "read",
            "edit",
            "write",
            "task_tracker",
            "think",
            "finish",
            "terminal_batch",
            "complexity_review",
        ]
    )
    builtins, engine_tools = _declared_tools(cfg)
    assert builtins == ["Bash", "Read", "Edit", "Write", "TodoWrite"]
    # ``finalize`` is always callable, even when the role predates it.
    assert engine_tools == [
        "mcp__nebuko__terminal_batch",
        "mcp__nebuko__complexity_review",
        "mcp__nebuko__finalize",
    ]


def test_build_agent_allows_the_declared_tools_and_guards_every_call() -> None:
    from types import SimpleNamespace

    from nebuko.engine.harness.claude_code import ClaudeCodeHarness

    cfg = SimpleNamespace(
        tools=["terminal", "read", "finalize"],
        mcp_servers=[],
        model_ref=None,
    )
    built = ClaudeCodeHarness().build_agent(
        role_name="dev", agent_cfg=cfg, prompt="p", agent_context=None
    )
    options = built.session.options
    assert options.tools == ["Bash", "Read"]
    assert options.allowed_tools == [
        "Bash",
        "Read",
        "mcp__nebuko__finalize",
    ]
    assert "PreToolUse" in options.hooks


def test_policy_hook_denies_tools_outside_the_closing_policy() -> None:
    import asyncio

    from nebuko.engine.harness.claude_code import _policy_hook

    session, _, _ = _session()
    hook = _policy_hook(session.policy).hooks[0]

    allowed = asyncio.run(hook({"tool_name": FINALIZE_TOOL}, None, {}))
    assert allowed == {}

    session.set_tool_policy(frozenset({FINALIZE_TOOL}))
    denied = asyncio.run(hook({"tool_name": "Bash"}, None, {}))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_declared_mcp_servers_become_stdio_configs(monkeypatch) -> None:
    from types import SimpleNamespace

    from nebuko.engine.harness.claude_code import _stdio_servers

    monkeypatch.setenv("BRAVE_API_KEY", "secret-value")
    cfg = SimpleNamespace(
        mcp_servers=[
            SimpleNamespace(
                name="brave-search",
                command="npx",
                args=["-y", "server"],
                env={"BRAVE_API_KEY": "BRAVE_API_KEY"},
            )
        ]
    )
    assert _stdio_servers(cfg) == {
        "brave-search": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "server"],
            "env": {"BRAVE_API_KEY": "secret-value"},
        }
    }


def test_declared_mcp_server_with_unset_env_is_skipped(monkeypatch) -> None:
    from types import SimpleNamespace

    from nebuko.engine.harness.claude_code import _stdio_servers

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    cfg = SimpleNamespace(
        mcp_servers=[
            SimpleNamespace(
                name="brave-search",
                command="npx",
                args=[],
                env={"BRAVE_API_KEY": "BRAVE_API_KEY"},
            )
        ]
    )
    assert _stdio_servers(cfg) == {}


def test_tool_results_surface_as_observation_events() -> None:
    from claude_agent_sdk import ToolResultBlock, UserMessage

    from nebuko.engine.harness.claude_code import _events_of

    events = _events_of(
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content=[{"type": "text", "text": "line one"}],
                    is_error=False,
                )
            ]
        )
    )
    assert [event.kind for event in events] == ["observation"]
    assert events[0].text == "line one"


def test_harness_build_conversation_wraps_the_built_session(monkeypatch) -> None:
    from types import SimpleNamespace

    from nebuko.engine.harness.claude_code import ClaudeCodeHarness

    harness = ClaudeCodeHarness()
    monkeypatch.setattr(
        harness,
        "build_agent",
        lambda **kwargs: SimpleNamespace(
            session=_session()[0], prompt=kwargs["prompt"]
        ),
    )
    conversation = harness.build_conversation(
        role_name="dev",
        agent_cfg=None,
        prompt="p",
        agent_context=SimpleNamespace(id="run-2", workspace="/tmp/repo"),
    )
    assert conversation.id == "run-2"
    assert isinstance(conversation.harness_closing_channel, ClaudeCodeClosingChannel)


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
