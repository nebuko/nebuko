"""Spec-scenario tests for the engine vocabulary (issue #590, plan section 3.1)."""

from __future__ import annotations

import dataclasses
import sys

import pytest

from nebuko.engine.models import (
    CommentEntry,
    DeterministicStep,
    ExitCode,
    RoutingDecision,
    RunOutcome,
    SkipKind,
    SkipOutcome,
    StepResult,
    Trigger,
)
from workflow_state.workflow_state import WorkflowState
from workflow_state import from_body


def _trigger(**overrides):
    values = {"author": "olivier-norture", "body": "@@dev fix it", "type": "issue_comment", "number": 590}
    values.update(overrides)
    return Trigger(**values)


def _bot_marker_history(count: int) -> list[CommentEntry]:
    """Bot comments carrying one ``bot-agent-`` marker each."""
    return [
        CommentEntry(
            author="bot",
            body="<!-- bot-agent-dev -->",
            created_at="2026-10-01T07:47:47Z",
        )
        for _ in range(count)
    ]


class TestTrigger:
    def test_field_order_matches_the_frozen_contract(self):
        assert [f.name for f in dataclasses.fields(Trigger)] == [
            "author",
            "body",
            "type",
            "number",
            "payload",
            "issue_body",
        ]

    def test_issue_body_defaults_to_empty(self):
        assert _trigger().issue_body == ""

    def test_issue_body_accepts_the_issue_record_body(self):
        assert _trigger(issue_body="the real task").issue_body == "the real task"

    def test_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _trigger().author = "someone-else"

    def test_payload_defaults_to_empty_and_is_not_shared(self):
        assert _trigger().payload == {}
        assert _trigger().payload is not _trigger().payload

    def test_payload_accepts_the_raw_event(self):
        assert _trigger(payload={"action": "created"}).payload == {"action": "created"}


class TestCommentEntry:
    def test_carries_author_body_and_created_at(self):
        entry = CommentEntry(author="bot", body="hi", created_at="2026-10-01T07:47:47Z")
        assert (entry.author, entry.body, entry.created_at) == ("bot", "hi", "2026-10-01T07:47:47Z")

    def test_is_frozen(self):
        entry = CommentEntry(author="bot", body="hi", created_at="2026-10-01T07:47:47Z")
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.body = "changed"


class TestRoutingDecision:
    def test_defaults_describe_a_plain_advance(self):
        decision = RoutingDecision(status="advance")
        assert (decision.agent, decision.complexity, decision.branch, decision.reason) == (
            None,
            "medium",
            None,
            None,
        )

    @pytest.mark.parametrize("status", ["advance", "loop", "hold"])
    def test_accepts_the_three_documented_statuses(self, status):
        assert RoutingDecision(status=status).status == status


class TestExitCode:
    def test_values_distinguish_the_three_terminal_outcomes(self):
        assert ExitCode.ADVANCE == 0
        assert ExitCode.ERROR == 1
        assert ExitCode.HOLD == 10
        assert ExitCode.TERMINAL == 20

    def test_is_an_int_enum_so_it_can_be_returned_as_a_process_code(self):
        assert int(ExitCode.HOLD) == 10


class TestRunOutcome:
    def test_field_order_matches_the_frozen_contract(self):
        assert [f.name for f in dataclasses.fields(RunOutcome)] == [
            "exit_code",
            "routing",
            "prompt",
            "posted_body",
            "state",
            "phases",
        ]

    def test_carries_the_routing_decision_and_the_emitted_phases(self):
        outcome = RunOutcome(
            exit_code=ExitCode.ADVANCE,
            routing=RoutingDecision(status="advance"),
            prompt="prompt",
            posted_body="body",
            state=None,
            phases=("normalize",),
        )
        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.phases == ("normalize",)


class _RecordingAdapter:
    """Minimal port stand-in that records delete calls."""

    def __init__(self, deleted: int = 5, error: Exception | None = None):
        self.deleted = deleted
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def delete_issue_comments(self, issue: str, from_comment: str = "") -> int:
        self.calls.append((issue, from_comment))
        if self.error is not None:
            raise self.error
        return self.deleted


class _CircuitBreakerAdapter:
    """Port stand-in recording the breaker-update state hand-off."""

    def __init__(self, state=None, history=None):
        self._state = state
        self._history = history or []
        self.posted: list[str] = []
        self.saved = None

    def load_state(self):
        return self._state

    def load_history(self, *, limit: int | None = None):
        return self._history

    def post(self, body: str) -> None:
        self.posted.append(body)

    def save_state(self, state) -> None:
        self.saved = state


class TestStepResult:
    def test_field_order_matches_the_frozen_contract(self):
        assert [f.name for f in dataclasses.fields(StepResult)] == [
            "step_kind",
            "ok",
            "detail",
            "affected",
        ]

    def test_is_frozen(self):
        result = StepResult(step_kind="delete_comments", ok=True, detail="done", affected=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.ok = False


class TestDeterministicStepFromResolved:
    def test_parses_the_delete_comments_contract(self):
        step = DeterministicStep.from_resolved(
            {"DELETE_COMMENTS": "true", "DELETE_COMMENTS_ISSUE": "590"}
        )
        assert step == DeterministicStep(kind="delete_comments", issue="590")

    def test_honors_the_from_boundary_when_present(self):
        step = DeterministicStep.from_resolved(
            {
                "DELETE_COMMENTS": "true",
                "DELETE_COMMENTS_ISSUE": "590",
                "DELETE_COMMENTS_FROM": "1234",
            }
        )
        assert step is not None
        assert step.from_comment == "1234"

    def test_parses_the_run_script_contract(self):
        step = DeterministicStep.from_resolved(
            {
                "RUN_SCRIPT": "true",
                "SCRIPT_PATH": "scripts/clean.sh",
                "SCRIPT_ARGS": "--all",
                "SCRIPT_TIMEOUT_MINUTES": "5",
            }
        )
        assert step == DeterministicStep(
            kind="run_script",
            script_path="scripts/clean.sh",
            script_args="--all",
            timeout_minutes=5,
        )

    def test_run_script_without_a_timeout_leaves_it_none(self):
        step = DeterministicStep.from_resolved(
            {"RUN_SCRIPT": "true", "SCRIPT_PATH": "scripts/clean.sh"}
        )
        assert step is not None
        assert step.timeout_minutes is None

    def test_parses_the_update_circuit_breaker_contract(self):
        step = DeterministicStep.from_resolved(
            {"UPDATE_CIRCUIT_BREAKER": "true", "UPDATE_CIRCUIT_BREAKER_VALUE": "35"}
        )
        assert step == DeterministicStep(
            kind="update_circuit_breaker", circuit_breaker_value=35
        )

    def test_update_circuit_breaker_non_numeric_value_defaults_to_zero(self):
        step = DeterministicStep.from_resolved(
            {"UPDATE_CIRCUIT_BREAKER": "true", "UPDATE_CIRCUIT_BREAKER_VALUE": "abc"}
        )
        assert step is not None
        assert step.circuit_breaker_value == 0

    def test_delete_wins_on_a_pathological_overlap(self):
        step = DeterministicStep.from_resolved(
            {
                "DELETE_COMMENTS": "true",
                "DELETE_COMMENTS_ISSUE": "590",
                "RUN_SCRIPT": "true",
                "SCRIPT_PATH": "scripts/clean.sh",
            }
        )
        assert step is not None
        assert step.kind == "delete_comments"

    @pytest.mark.parametrize(
        "resolved",
        [
            {},
            {"AGENT_SELECTED": "true", "SHOULD_SKIP": "false"},
            {"SKIP_AGENT": "true", "REASON": "nothing to do"},
            {"DELETE_COMMENTS": "false"},
            {"RUN_SCRIPT": "false"},
            {"UPDATE_CIRCUIT_BREAKER": "false"},
        ],
    )
    def test_returns_none_when_no_step_is_present(self, resolved):
        assert DeterministicStep.from_resolved(resolved) is None

    def test_returns_none_for_a_non_mapping(self):
        assert DeterministicStep.from_resolved(None) is None


class TestDeterministicStepExecute:
    def test_delete_delegates_to_the_adapter_and_counts(self):
        adapter = _RecordingAdapter(deleted=4)
        result = DeterministicStep(kind="delete_comments", issue="590").execute(adapter)

        assert adapter.calls == [("590", "")]
        assert result == StepResult(
            step_kind="delete_comments",
            ok=True,
            detail="deleted 4 comments on issue 590",
            affected=4,
        )

    def test_delete_forwards_the_from_boundary(self):
        adapter = _RecordingAdapter(deleted=2)
        result = DeterministicStep(
            kind="delete_comments", issue="590", from_comment="1234"
        ).execute(adapter)

        assert adapter.calls == [("590", "1234")]
        assert result.affected == 2

    def test_delete_transport_failure_becomes_a_result_not_a_crash(self):
        adapter = _RecordingAdapter(error=RuntimeError("gh exploded"))
        result = DeterministicStep(kind="delete_comments", issue="590").execute(adapter)

        assert result.ok is False
        assert result.affected == 0
        assert "gh exploded" in result.detail

    def test_run_script_reports_the_exit_code(self):
        result = DeterministicStep(
            kind="run_script",
            script_path=sys.executable,
            script_args='-c "import sys; sys.exit(3)"',
        ).execute(_RecordingAdapter())

        assert result.ok is False
        assert result.affected == 3
        assert "exited with code 3" in result.detail

    def test_run_script_success_is_ok(self):
        result = DeterministicStep(
            kind="run_script", script_path=sys.executable, script_args='-c "pass"'
        ).execute(_RecordingAdapter())

        assert result.ok is True
        assert result.affected == 0

    def test_run_script_oserror_becomes_a_result_not_a_crash(self):
        result = DeterministicStep(
            kind="run_script", script_path="/nonexistent/nebuko-script"
        ).execute(_RecordingAdapter())

        assert result.ok is False
        assert result.affected == 0

    def test_update_circuit_breaker_persists_the_limit_and_posts_state(self):
        adapter = _CircuitBreakerAdapter()
        result = DeterministicStep(
            kind="update_circuit_breaker", circuit_breaker_value=35
        ).execute(adapter)

        assert result.ok is True
        assert result.affected == 35
        assert adapter.saved is not None
        assert adapter.saved.circuit_breaker.limit == 35
        # The confirmation must carry the state so the next run's guard reads
        # the override back from the comment history.
        embedded = from_body(adapter.posted[0])
        assert embedded is not None
        assert embedded.circuit_breaker.limit == 35
        assert "Circuit-breaker limit set to **35**" in adapter.posted[0]

    def test_update_circuit_breaker_warns_when_value_does_not_clear_usage(self):
        adapter = _CircuitBreakerAdapter(
            state=WorkflowState(), history=_bot_marker_history(6)
        )

        result = DeterministicStep(
            kind="update_circuit_breaker", circuit_breaker_value=2
        ).execute(adapter)

        assert result.ok is True
        assert "still tripped" in adapter.posted[0]

    def test_update_circuit_breaker_reports_unlocked_when_leaving_headroom(self):
        adapter = _CircuitBreakerAdapter(
            state=WorkflowState(), history=_bot_marker_history(6)
        )

        DeterministicStep(
            kind="update_circuit_breaker", circuit_breaker_value=10
        ).execute(adapter)

        assert "the issue is unlocked" in adapter.posted[0]


class TestSkipOutcome:
    """The typed guards-skip verdict carried to the exit path (issue #853)."""

    def test_benign_kinds_map_to_exit_zero(self):
        for kind in (SkipKind.NO_NEXT_AGENT, SkipKind.TERMINAL):
            outcome = SkipOutcome(kind=kind, reason="done")

            assert outcome.is_benign is True
            assert outcome.exit_code == ExitCode.ADVANCE

    def test_config_error_is_not_benign_and_keeps_terminal(self):
        outcome = SkipOutcome(kind=SkipKind.CONFIG_ERROR, reason="workflow not found")

        assert outcome.is_benign is False
        assert outcome.exit_code == ExitCode.TERMINAL

    def test_from_verdict_reads_the_kind(self):
        outcome = SkipOutcome.from_verdict(
            {
                "SKIP_AGENT": "true",
                "REASON": "No valid task found",
                "SKIP_KIND": "no_next_agent",
            }
        )

        assert outcome.kind is SkipKind.NO_NEXT_AGENT
        assert outcome.reason == "No valid task found"
        assert outcome.is_benign is True

    def test_from_verdict_defaults_to_config_error_when_unclassified(self):
        outcome = SkipOutcome.from_verdict(
            {"SKIP_AGENT": "true", "REASON": "nothing to do"}
        )

        assert outcome.kind is SkipKind.CONFIG_ERROR
        assert outcome.exit_code == ExitCode.TERMINAL

    def test_from_verdict_defaults_to_config_error_on_unknown_kind(self):
        outcome = SkipOutcome.from_verdict({"SKIP_KIND": "banana", "REASON": "?"})

        assert outcome.kind is SkipKind.CONFIG_ERROR

    def test_is_frozen(self):
        outcome = SkipOutcome(kind=SkipKind.TERMINAL, reason="done")

        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.reason = "changed"
