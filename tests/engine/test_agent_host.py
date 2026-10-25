"""Unit tests for the in-process agent host (issue #740).

The host owns the in-process run: it imports the frozen legacy assembly, drives
its steps with a temporary cwd, and reads the run artefacts back with the
captured deliverable attached. These tests drive it with a fake pipeline so the
assembly never reaches the LLM, while still exercising the real code path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nebuko.engine.agent_host import (
    InProcessAgentHost,
    _FINALIZE_BODY_ATTR,
    _FINALIZE_PROMPT,
)
from nebuko.engine.agent_runner import EngineAgentUnavailable
from workflow_state import Appreciation, TurnOutcome, TurnStatus

# The engine-measured run status the host folds into the outcome (issue #964).
from agent_script.features.run_agent.run_outcome import OutcomeStatus

_SUCCESS = OutcomeStatus.SUCCESS

ENTRY_POINT = (
    Path("src") / "nebuko" / "engine" / "lib" / "agent_script" / "agent_script.py"
)


def _write_entry_point(repo_root: Path) -> None:
    """Create the entry point so the host's existence check passes.

    The host imports the assembly, never executes this file, so its contents are
    irrelevant — only the path matters.
    """
    script = repo_root / ENTRY_POINT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# imported, never executed\n", encoding="utf-8")


def _agent_message(text: str) -> object:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message

    return MessageEvent(llm_message=Message(role="assistant", content=text), source="agent")


def _end_tool_turn(tool_name: str, text: str) -> object:
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    return ActionEvent(
        source="agent",
        tool_name=tool_name,
        thought=[TextContent(text=text)],
        tool_call_id=f"tc-{tool_name}",
        tool_call=MessageToolCall(
            id=f"tc-{tool_name}", name=tool_name, arguments="{}", origin="completion"
        ),
        llm_response_id="r1",
    )


def _finish_report_event(message: str) -> object:
    """A real SDK ``finish`` action carrying the typed report message."""
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent
    from openhands.sdk.tool.builtins.finish import FinishAction

    return ActionEvent(
        source="agent",
        tool_name="finish",
        thought=[TextContent(text="")],
        action=FinishAction(message=message),
        tool_call_id="tc-report-finish",
        tool_call=MessageToolCall(
            id="tc-report-finish", name="finish", arguments="{}", origin="completion"
        ),
        llm_response_id="r2",
    )


def _finalize_report_event(report: str, **outcome: Any) -> object:
    """A real ``finalize`` action: the turn text is the report, the args the outcome.

    Ellipsis-free by construction: this is the one terminal channel every step
    ends through (issue #1033), so the closing turn carries both artifacts. The
    SDK splits the ``response_schema`` fields out of the action arguments and
    hands them over as the action's structured output, which is mirrored here.
    """
    from agent_script.features.finalize_tool.finalize_tool import FinalizeAction
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    action = FinalizeAction()
    action._structured_output = {"turn_outcome": outcome}
    return ActionEvent(
        source="agent",
        tool_name="finalize",
        thought=[TextContent(text=report)],
        action=action,
        tool_call_id="tc-report-finalize",
        tool_call=MessageToolCall(
            id="tc-report-finalize",
            name="finalize",
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="r2",
    )


def _run_recorded_finalize_actions(events: list[Any], conversation: Any) -> None:
    """Run the real ``finalize`` executor over the scripted actions (issue #1033).

    The SDK executes the tool after the turn; the scripted conversation replays
    the turn without the SDK, so the executor is invoked here to record the
    typed outcome in the run-scoped registry exactly as a live run would.
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from agent_script.features.finalize_tool.finalize_tool import FinalizeExecutor

    executor = FinalizeExecutor(conversation_id="conv-test")
    for event in events:
        if getattr(event, "tool_name", None) != "finalize":
            continue
        action = getattr(event, "action", None)
        if action is not None:
            executor(action, conversation)


class _FakeAgent:
    """Minimal agent exposing the SDK's private resolved-tools map."""

    def __init__(self, tools: dict[str, Any]) -> None:
        import threading

        self._tools = dict(tools)
        self._tools_lock = threading.RLock()


class _FakeConversation:
    """Conversation stand-in that replays a scripted engine-elicited turn.

    ``events`` is the work loop's stream; ``run`` appends ``report_events`` —
    the report turn — exactly as the SDK appends turns to the same conversation.
    """

    def __init__(
        self,
        *,
        events: list[Any],
        report_events: list[Any] | None = None,
        tools: dict[str, Any] | None = None,
        fail_send: bool = False,
    ) -> None:
        self.id = "conv-test"
        self.state = SimpleNamespace(events=list(events))
        self.agent = _FakeAgent(tools or {})
        self._report_events = list(report_events or [])
        self._fail_send = fail_send
        self.prompts: list[str] = []
        self.tools_seen_during_run: dict[str, Any] | None = None

    def send_message(self, prompt: str) -> None:
        if self._fail_send:
            raise RuntimeError("llm unavailable")
        self.prompts.append(prompt)

    def run(self) -> None:
        self.tools_seen_during_run = dict(self.agent._tools)
        self.state.events.extend(self._report_events)
        _run_recorded_finalize_actions(self._report_events, self)

    def interrupt(self) -> None:  # pragma: no cover - the timeout path
        pass


def _host_for(
    repo_root: Path,
    conversation: object,
    *,
    latest_answer: str,
    state_turn_outcome: dict[str, Any] | None = None,
) -> InProcessAgentHost:
    """A host whose ``run_agent`` returns ``conversation``."""

    def _run_agent(llm_config, prompt, args, agent_cfg=None, workspace=None):
        return conversation

    return _host(
        repo_root,
        events=[],
        latest_answer=latest_answer,
        run_agent=_run_agent,
        state_turn_outcome=state_turn_outcome,
    )


def _host(
    repo_root: Path,
    *,
    events: list[Any],
    latest_answer: str,
    run_agent: Any = None,
    state_turn_outcome: dict[str, Any] | None = None,
) -> InProcessAgentHost:
    """Build a host whose injected pipeline writes real artefact files."""

    def _build_output(conversation, metrics, args, prev_state=None):
        Path("latest_answer.md").write_text(latest_answer, encoding="utf-8")
        Path("final_message.txt").write_text("finish note", encoding="utf-8")
        state: dict[str, Any] = {
            "version": 1,
            "workflow": "w",
            "step": "s",
            "agent": "dev",
        }
        if state_turn_outcome is not None:
            state["turn_outcome"] = state_turn_outcome
        Path("workflow_state.json").write_text(json.dumps(state), encoding="utf-8")

    def _default_run_agent(llm_config, prompt, args, agent_cfg=None, workspace=None):
        # The engine-elicited closing turn calls ``send_message``/``run`` on the
        # conversation, so the work-loop stand-in must expose the SDK surface
        # (a bare ``state.events`` namespace no longer suffices, issue #1033).
        return _FakeConversation(events=events)

    pipeline = SimpleNamespace(
        build_output=_build_output,
        collect_and_save_metrics=lambda conversation, agent_cfg: object(),
        load_prev_state_or_recover=lambda: None,
        resolve_llm_config=lambda args, prompt: (object(), prompt, object()),
        run_agent=run_agent or _default_run_agent,
    )
    return InProcessAgentHost(repo_root=repo_root, load_pipeline=lambda: pipeline)


def test_missing_entry_point_declares_the_host_unavailable(tmp_path: Path) -> None:
    """A host without the legacy checkout must not crash — it declares itself out."""
    with pytest.raises(EngineAgentUnavailable):
        InProcessAgentHost(
            repo_root=tmp_path / "empty", load_pipeline=lambda: None
        ).run("prompt")


def test_captures_the_deliverable_from_the_end_tool_boundary(tmp_path: Path) -> None:
    """The clobbered latest_answer.md is bypassed by the boundary capture."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    host = _host(
        repo_root,
        events=[_agent_message("Full architecture proposal"), _end_tool_turn("finish", "short note")],
        latest_answer="short note",
    )

    result = host.run("prompt")

    assert result["deliverable"] == "Full architecture proposal"
    # ``answer`` stays the legacy value: routing/validation read the dispatch
    # sentinel out of it (issue #740 must not reroute a run).
    assert result["answer"] == "short note"
    assert result["final_message"] == "finish note"
    assert result["state"].agent == "dev"


def test_a_non_terminal_tool_turn_is_never_the_deliverable(tmp_path: Path) -> None:
    """#1033: only the closing turn's text is the report; a work-loop tool turn
    that merely carried prose (an intermediate tool call) is not captured."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    host = _host(
        repo_root,
        events=[
            _end_tool_turn("terminal", "Full proposal"),
            _end_tool_turn("finish", "note"),
        ],
        latest_answer="note",
    )

    result = host.run("prompt")

    assert result["deliverable"] == "note"
    assert result["answer"] == "note"


def test_falls_back_to_the_legacy_answer_for_an_empty_stream(tmp_path: Path) -> None:
    """No agent turn to classify → the legacy latest answer is the deliverable."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    host = _host(repo_root, events=[], latest_answer="legacy answer")

    assert host.run("prompt")["deliverable"] == "legacy answer"


def test_a_run_failure_propagates_and_is_not_an_unavailable_signal(tmp_path: Path) -> None:
    """A failure after the run started must not trigger a second (fallback) run."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)

    def _boom(*args, **kwargs):
        raise RuntimeError("llm exploded")

    host = _host(repo_root, events=[], latest_answer="", run_agent=_boom)

    with pytest.raises(RuntimeError) as excinfo:
        host.run("prompt")

    assert not isinstance(excinfo.value, EngineAgentUnavailable)


def test_run_binds_the_explicit_repo_root_workspace(tmp_path: Path) -> None:
    """#766: the agent operates on the real checkout, not the host's temp cwd.

    Artefact staging still runs under a temporary cwd, but the conversation
    workspace — and therefore the wind-down's git ops — must target the repo.
    """
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    seen: dict[str, Any] = {}

    def _capture_run_agent(llm_config, prompt, args, agent_cfg=None, workspace=None):
        seen["workspace"] = workspace
        seen["cwd"] = os.getcwd()
        return _FakeConversation(events=[])

    host = _host(repo_root, events=[], latest_answer="", run_agent=_capture_run_agent)

    host.run("prompt")

    assert os.fspath(seen["workspace"]) == os.fspath(repo_root)
    assert os.fspath(seen["cwd"]) != os.fspath(repo_root)


class TestEngineElicitedClosingTurn:
    """#1033: one closing ``finalize`` turn ends every step, carrying both artifacts."""

    def test_posts_the_closing_turn_text_as_the_deliverable(
        self, tmp_path: Path
    ) -> None:
        """Without an ``output:`` shape the turn text *is* the posted report."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        report = "## Wind-down\n\n- Rebased on origin/main\n- Tests: 115 passed"
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "Work pushed and PR updated.")],
            report_events=[_finalize_report_event(report, appreciation="approved")],
        )
        host = _host_for(
            repo_root, conversation, latest_answer="Work pushed and PR updated."
        )

        result = host.run("prompt")

        assert result["deliverable"] == report
        # ``answer`` stays the legacy value so routing is unchanged.
        assert result["answer"] == "Work pushed and PR updated."
        assert conversation.prompts == [_FINALIZE_PROMPT.format(sections="")]
        assert getattr(conversation, _FINALIZE_BODY_ATTR) == report

    def test_harvests_the_typed_turn_outcome_payload(self, tmp_path: Path) -> None:
        """The ``finalize`` turn outcome (not prose) carries the verdict."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finalize_report_event(
                    "Full report",
                    appreciation="approved",
                    complexity="high",
                    note="ship it",
                ),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        result = host.run("prompt")

        assert result["turn_outcome"] == {
            "status": "completed",
            "appreciation": "approved",
            "complexity": "high",
            "note": "ship it",
        }

    def test_restricts_the_tool_surface_to_the_terminal_gate(
        self, tmp_path: Path
    ) -> None:
        """The closing turn exposes only the terminal ``finalize`` gate (issue #1071)."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        tools = {
            "terminal": object(),
            "file_editor": object(),
            "finish": object(),
            "finalize": object(),
        }
        conversation = _FakeConversation(
            events=[],
            report_events=[_finalize_report_event("report", appreciation="approved")],
            tools=tools,
        )
        host = _host_for(repo_root, conversation, latest_answer="")

        host.run("prompt")

        assert set(conversation.tools_seen_during_run) == {"finalize"}
        assert set(conversation.agent._tools) == set(tools)

    def test_reports_an_empty_tool_map_without_restricting(self, tmp_path: Path) -> None:
        """A host whose agent exposes no resolved tools still runs the turn."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[],
            report_events=[_finalize_report_event("report", appreciation="approved")],
            tools={},
        )
        host = _host_for(repo_root, conversation, latest_answer="")

        assert host.run("prompt")["deliverable"] == "report"

    def test_failed_closing_turn_falls_back_to_the_boundary_capture(
        self, tmp_path: Path
    ) -> None:
        """A failed closing turn never aborts the run; the freeze chain holds."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[
                _agent_message("Full architecture proposal"),
                _end_tool_turn("finish", "short note"),
            ],
            fail_send=True,
        )
        host = _host_for(repo_root, conversation, latest_answer="short note")

        result = host.run("prompt")

        assert result["deliverable"] == "Full architecture proposal"
        assert getattr(conversation, _FINALIZE_BODY_ATTR, None) is None
        assert "turn_outcome" not in result

    def test_exposes_a_hold_with_awaiting_input(self, tmp_path: Path) -> None:
        """#1033: ``hold`` means the agent needs human input, nothing else."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finalize_report_event(
                    "Full report",
                    appreciation="hold",
                    hold_kind="awaiting_input",
                    note="which plan?",
                ),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        result = host.run("prompt")

        # The host's payload carries the engine-measured status; the agent's
        # own payload never does, so strip it before the agent-facing decode.
        agent_payload = {
            key: value
            for key, value in result["turn_outcome"].items()
            if key not in ("status", "incomplete_reason", "agent_call")
        }
        outcome = TurnOutcome.from_agent_payload(agent_payload, observed=_SUCCESS)
        assert result["turn_outcome"]["status"] == "completed"
        assert outcome.status is TurnStatus.COMPLETED
        assert outcome.appreciation is Appreciation.HOLD
        assert outcome.hold_kind.value == "awaiting_input"
        assert outcome.note == "which plan?"

    def test_agent_call_names_the_hand_off_target(self, tmp_path: Path) -> None:
        """#1033: ``turn_outcome.agent_call`` is the only hand-off channel."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finalize_report_event(
                    "Full report",
                    appreciation="approved",
                    complexity="high",
                    agent_call={"agent_name": "context", "task": "collect facts"},
                ),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        result = host.run("prompt")

        assert result["turn_outcome"]["agent_call"] == {
            "agent_name": "context",
            "task": "collect facts",
        }
        assert result["turn_outcome"]["complexity"] == "high"

    def test_no_turn_outcome_key_without_a_closing_turn(self, tmp_path: Path) -> None:
        """No closing turn means no typed outcome to hand the engine."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        host = _host(
            repo_root,
            events=[_end_tool_turn("finish", "note")],
            latest_answer="note",
        )

        assert "turn_outcome" not in host.run("prompt")
