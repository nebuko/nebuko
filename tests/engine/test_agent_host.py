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
    _REPORT_FUNCTION_CALL_ATTR,
    _REPORT_PAYLOAD_ATTR,
    _REPORT_PROMPT,
    _REPORT_ROUTING_ATTR,
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


def _dispatch_report_event(**fields: Any) -> object:
    """A real ``dispatch_next`` action carrying the factual turn outcome."""
    from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
        TurnOutcomeAction,
    )
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    return ActionEvent(
        source="agent",
        tool_name="dispatch_next",
        thought=[TextContent(text="")],
        action=TurnOutcomeAction(**fields),
        tool_call_id="tc-report-dispatch",
        tool_call=MessageToolCall(
            id="tc-report-dispatch",
            name="dispatch_next",
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="r2",
    )


def _call_agent_report_event(**fields: Any) -> object:
    """A real ``call_agent`` action carrying the typed hand-off (issue #876)."""
    from agent_script.features.call_agent_tool.call_agent_tool import CallAgentAction
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    return ActionEvent(
        source="agent",
        tool_name="call_agent",
        thought=[TextContent(text="")],
        action=CallAgentAction(**fields),
        tool_call_id="tc-report-call-agent",
        tool_call=MessageToolCall(
            id="tc-report-call-agent",
            name="call_agent",
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="r2",
    )


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
        return SimpleNamespace(state=SimpleNamespace(events=events))

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


def test_recovers_a_deliverable_sharing_the_end_tool_turn(tmp_path: Path) -> None:
    """#555/#738: the deliverable shares the ``dispatch_next`` turn, so it is not
    a ``MessageEvent``; the legacy ``latest_answer.md`` holds the finish note."""
    repo_root = tmp_path / "checkout"
    _write_entry_point(repo_root)
    host = _host(
        repo_root,
        events=[_end_tool_turn("dispatch_next", "Full proposal"), _end_tool_turn("finish", "note")],
        latest_answer="note",
    )

    result = host.run("prompt")

    assert result["deliverable"] == "Full proposal"
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
        return SimpleNamespace(state=SimpleNamespace(events=[]))

    host = _host(repo_root, events=[], latest_answer="", run_agent=_capture_run_agent)

    host.run("prompt")

    assert os.fspath(seen["workspace"]) == os.fspath(repo_root)
    assert os.fspath(seen["cwd"]) != os.fspath(repo_root)


class TestEngineElicitedReportTurn:
    """#822/#830: the engine takes the hand at run end to fill the typed slots."""

    def test_posts_the_typed_finish_message_over_the_work_loop_note(
        self, tmp_path: Path
    ) -> None:
        """The report turn's ``finish.message`` is the posted deliverable — the
        exact #822 fix (the work loop's latest_answer.md is a one-liner)."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        report = "## Wind-down\n\n- Rebased on origin/main\n- Tests: 115 passed"
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "Work pushed and PR updated.")],
            report_events=[_finish_report_event(report)],
        )
        host = _host_for(
            repo_root, conversation, latest_answer="Work pushed and PR updated."
        )

        result = host.run("prompt")

        assert result["deliverable"] == report
        # ``answer`` stays the legacy value so routing is unchanged.
        assert result["answer"] == "Work pushed and PR updated."
        assert conversation.prompts == [_REPORT_PROMPT]
        assert getattr(conversation, _REPORT_PAYLOAD_ATTR).fields["message"] == report

    def test_harvests_the_typed_turn_outcome_payload(self, tmp_path: Path) -> None:
        """``dispatch_next``'s typed fields (not prose) carry the turn outcome."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finish_report_event("Full report"),
                _dispatch_report_event(
                    appreciation="approved", complexity="high", note="ship it"
                ),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        host.run("prompt")

        routing = getattr(conversation, _REPORT_ROUTING_ATTR)
        assert routing.payload_kind == "routing"
        assert routing.fields["appreciation"] == "approved"
        assert routing.fields["complexity"] == "high"
        assert routing.fields["note"] == "ship it"

    def test_restricts_the_tool_surface_to_the_control_plane(
        self, tmp_path: Path
    ) -> None:
        """The report turn exposes only the engine tools, then restores the map."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        tools = {
            "terminal": object(),
            "file_editor": object(),
            "finish": object(),
            "dispatch_next": object(),
        }
        conversation = _FakeConversation(
            events=[], report_events=[_finish_report_event("report")], tools=tools
        )
        host = _host_for(repo_root, conversation, latest_answer="")

        host.run("prompt")

        assert set(conversation.tools_seen_during_run) == {"finish", "dispatch_next"}
        assert set(conversation.agent._tools) == set(tools)

    def test_reports_an_empty_tool_map_without_restricting(self, tmp_path: Path) -> None:
        """A host whose agent exposes no resolved tools still runs the turn."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[], report_events=[_finish_report_event("report")], tools={}
        )
        host = _host_for(repo_root, conversation, latest_answer="")

        assert host.run("prompt")["deliverable"] == "report"

    def test_failed_report_turn_falls_back_to_the_boundary_capture(
        self, tmp_path: Path
    ) -> None:
        """A failed report turn never aborts the run; the freeze chain holds."""
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
        assert getattr(conversation, _REPORT_PAYLOAD_ATTR, None) is None
        assert getattr(conversation, _REPORT_ROUTING_ATTR, None) is None

    def test_exposes_the_typed_turn_outcome_to_the_engine(
        self, tmp_path: Path
    ) -> None:
        """#830: ``route`` reads the typed tool call, not the report-turn prose.

        The report turn replaces ``final_message.txt`` — the artefact ``route``
        used to scrape for the legacy sentinel — so without this hand-off a
        ``hold`` run would silently default to ``approved``.
        """
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finish_report_event("Full report"),
                _dispatch_report_event(
                    appreciation="hold",
                    hold_kind="awaiting_approval",
                    complexity="high",
                    note="await approval",
                ),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        result = host.run("prompt")

        assert result["turn_outcome"] == {
            "appreciation": "hold",
            "hold_kind": "awaiting_approval",
            "complexity": "high",
            "note": "await approval",
        }
        outcome = TurnOutcome.from_agent_payload(
            result["turn_outcome"], observed=_SUCCESS
        )
        assert outcome.status is TurnStatus.COMPLETED
        assert outcome.appreciation is Appreciation.HOLD
        assert outcome.hold_kind.value == "awaiting_approval"
        assert outcome.note == "await approval"

    def test_typed_turn_outcome_beats_the_state_fallback(
        self, tmp_path: Path
    ) -> None:
        """The report turn's own field wins; an omitted field is inherited.

        ``build_output`` resolves the run's turn outcome from the typed
        ``dispatch_next`` tool into the workflow state. The report turn's
        ``dispatch_next`` is authoritative for the fields it sets, while a
        field it omits (here the cross-agent target) still falls back to that
        resolution instead of silently dropping the hand-off (issue #830).
        """
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finish_report_event("Full report"),
                _dispatch_report_event(appreciation="hold", note="await approval"),
            ],
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="note",
            state_turn_outcome={
                "status": "completed",
                "appreciation": "approved",
                "required_agent": "review",
            },
        )

        result = host.run("prompt")

        assert result["turn_outcome"] == {
            "status": "completed",
            "appreciation": "hold",
            "required_agent": "review",
            "note": "await approval",
        }

    def test_report_turn_required_agent_reaches_the_outcome(
        self, tmp_path: Path
    ) -> None:
        """#830: a report-turn ``dispatch_next(required_agent=...)`` survives.

        The report turn is the sole source of the typed fact after a report
        run, so its cross-agent override must reach the turn outcome.
        """
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finish_report_event("Full report"),
                _dispatch_report_event(
                    appreciation="approved", required_agent="review", pin=True
                ),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        result = host.run("prompt")

        assert result["turn_outcome"]["required_agent"] == "review"
        assert result["turn_outcome"]["pin"] == "true"
        outcome = TurnOutcome.from_agent_payload(result["turn_outcome"], observed=_SUCCESS)
        assert outcome.required_agent == "review"

    def test_harvests_the_typed_call_agent_handoff(self, tmp_path: Path) -> None:
        """A report-turn ``call_agent`` is harvested as the typed hand-off."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finish_report_event("Full report"),
                _call_agent_report_event(agent_name="context", complexity="high"),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        host.run("prompt")

        handoff = getattr(conversation, _REPORT_FUNCTION_CALL_ATTR)
        assert handoff.fields["agent_name"] == "context"
        assert handoff.fields["complexity"] == "high"

    def test_report_turn_call_agent_reaches_the_turn_outcome(
        self, tmp_path: Path
    ) -> None:
        """#876: a report-turn ``call_agent`` names the exact next agent.

        Without the structural merge the imperative hand-off would be dropped
        and the run would revert to the workflow's default next step.
        """
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finish_report_event("Full report"),
                _call_agent_report_event(agent_name="context", complexity="high"),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        result = host.run("prompt")

        assert result["turn_outcome"]["required_agent"] == "context"
        assert result["turn_outcome"]["complexity"] == "high"
        outcome = TurnOutcome.from_agent_payload(result["turn_outcome"], observed=_SUCCESS)
        assert (outcome.required_agent, result["turn_outcome"]["complexity"]) == (
            "context",
            "high",
        )

    def test_report_turn_call_agent_target_beats_a_state_target(
        self, tmp_path: Path
    ) -> None:
        """The imperative hand-off outranks the state-derived target (#876)."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[
                _finish_report_event("Full report"),
                _dispatch_report_event(
                    appreciation="approved", required_agent="review"
                ),
                _call_agent_report_event(agent_name="context"),
            ],
        )
        host = _host_for(repo_root, conversation, latest_answer="note")

        result = host.run("prompt")

        assert result["turn_outcome"]["required_agent"] == "context"
        outcome = TurnOutcome.from_agent_payload(result["turn_outcome"], observed=_SUCCESS)
        assert outcome.required_agent == "context"

    def test_routes_from_the_state_when_the_report_returns_only_finish(
        self, tmp_path: Path
    ) -> None:
        """A report turn with no ``dispatch_next`` still routes structurally.

        ``build_output`` resolves the run's turn outcome from the typed
        ``dispatch_next`` tool into the workflow state; the host hands that
        resolution to ``route`` so the ``AGENT_RUN_CONFIG`` sentinel is never
        needed after a report turn.
        """
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        conversation = _FakeConversation(
            events=[_end_tool_turn("finish", "note")],
            report_events=[_finish_report_event("Full report")],
        )
        host = _host_for(
            repo_root,
            conversation,
            latest_answer="note",
            state_turn_outcome={
                "status": "completed",
                "appreciation": "hold",
                "hold_kind": "awaiting_approval",
                "note": "await approval",
            },
        )

        result = host.run("prompt")

        assert result["turn_outcome"] == {
            "status": "completed",
            "appreciation": "hold",
            "hold_kind": "awaiting_approval",
            "note": "await approval",
        }

    def test_no_turn_outcome_key_without_a_report_turn(self, tmp_path: Path) -> None:
        """The legacy path is untouched: no report turn, no typed outcome."""
        repo_root = tmp_path / "checkout"
        _write_entry_point(repo_root)
        host = _host(
            repo_root,
            events=[_end_tool_turn("finish", "note")],
            latest_answer="note",
        )

        assert "turn_outcome" not in host.run("prompt")
