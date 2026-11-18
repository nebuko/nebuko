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


def test_result_message_feeds_the_conversation_stats() -> None:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    from nebuko.engine.harness.claude_code import ClaudeCodeConversation, _events_of

    session, _, _ = _session()
    conversation = ClaudeCodeConversation(
        session=session,
        closing_channel=ClaudeCodeClosingChannel(),
        workspace="/tmp/repo",
    )
    for event in _events_of(
        AssistantMessage(content=[TextBlock(text="hi")], model="claude-sonnet-x")
    ):
        session._emit(event)
    for event in _events_of(
        ResultMessage(
            subtype="success",
            duration_ms=1000,
            duration_api_ms=900,
            is_error=False,
            num_turns=1,
            session_id="s1",
            total_cost_usd=0.5,
            usage={
                "input_tokens": 100,
                "cache_read_input_tokens": 300,
                "cache_creation_input_tokens": 50,
                "output_tokens": 40,
            },
            result="done",
        )
    ):
        session._emit(event)

    metrics = conversation.conversation_stats.get_combined_metrics()
    usage = metrics.accumulated_token_usage
    assert metrics.model_name == "claude-sonnet-x"
    assert usage.prompt_tokens == 450
    assert usage.completion_tokens == 40
    assert usage.cache_read_tokens == 300
    assert usage.cache_write_tokens == 50
    assert metrics.accumulated_cost == 0.5


def test_collect_llm_metrics_reports_claude_code_run(monkeypatch) -> None:
    from nebuko.engine.harness.claude_code import ClaudeCodeConversation
    from nebuko.engine.lib.agent_script.sub_domains.metrics.metrics_collector import (
        collect_llm_metrics,
    )

    monkeypatch.delenv("ROLE_NAME", raising=False)
    session, client, _ = _session()
    conversation = ClaudeCodeConversation(
        session=session,
        closing_channel=ClaudeCodeClosingChannel(),
        workspace="/tmp/repo",
        run_id="run-cc",
    )

    def _turn(_client):
        session._emit(HarnessEvent(kind="commentary", text="work", data={"model": "claude-x"}))
        session._emit(
            HarnessEvent(
                kind="result",
                text="done",
                data={
                    "usage": {"input_tokens": 150000, "output_tokens": 10},
                    "total_cost_usd": 1.25,
                    "model_usage": {},
                },
            )
        )

    client._on_query = _turn
    session.send_message("go")
    session.run()

    metrics = collect_llm_metrics(conversation)
    assert metrics.error is None
    assert metrics.model_name == "claude-x"
    assert metrics.input_tokens == 150000
    assert metrics.output_tokens == 10
    assert metrics.cost == 1.25
    assert metrics.start_time and metrics.end_time
    assert metrics.start_time != metrics.end_time
    assert metrics.wall_clock_ms >= 0


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


_TOKEN = "sk-ant-oat01-SECRET-VALUE"


class _ErrorResultClient(_FakeClient):
    """Replays one claude-code error result per turn, counting the turns."""

    def __init__(self, text: str, *, is_error: bool = True) -> None:
        super().__init__()
        self.turns = 0
        self._text = text
        self._is_error = is_error

    async def receive_response(self):
        from claude_agent_sdk import ResultMessage

        self.turns += 1
        yield ResultMessage(
            subtype="error",
            duration_ms=10,
            duration_api_ms=10,
            is_error=self._is_error,
            num_turns=1,
            session_id="s1",
            total_cost_usd=0.0,
            usage={},
            result=self._text,
        )


def test_401_result_raises_terminal_auth_error_once() -> None:
    from nebuko.engine.lib.agent_script.features.run_agent.run_failure import (
        HarnessAuthError,
        ProviderFailure,
    )

    session = ClaudeCodeSession(options=None, sink=FinalizeSink())
    client = _ErrorResultClient(
        "Failed to authenticate. API Error: 401 OAuth access token is invalid."
    )
    session._client = client

    with pytest.raises(HarnessAuthError) as raised:
        session.run(timeout_s=5)

    assert isinstance(raised.value, ProviderFailure)
    assert client.turns == 1
    assert _TOKEN not in str(raised.value)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in str(raised.value)


def test_other_error_results_do_not_raise_auth_error() -> None:
    session = ClaudeCodeSession(options=None, sink=FinalizeSink())
    session._client = _ErrorResultClient("tool crashed")

    session.run(timeout_s=5)


def test_auth_failure_notice_names_the_secret_and_omits_the_token(monkeypatch) -> None:
    from nebuko.engine.lib.agent_script.features.run_agent.run_failure import (
        harness_auth_message,
    )

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _TOKEN)
    message = harness_auth_message("acme/widgets")

    assert message.startswith("claude-code authentication failed (401)")
    assert "claude setup-token" in message
    assert message.endswith("update the secret in acme/widgets.")
    assert _TOKEN not in message
