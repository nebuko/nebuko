"""Red tests for the agent answer footer (issue #1159).

Contract: the engine appends a one-line footer to every *agent* answer —
never to script-step comments or engine output — rendered at publish time
from the same ``WorkflowState`` that feeds the collapsed header block:

    ``{workflow} | {steps} | {status} | {hint}``

Each test below names its given/when/then scenario per the architect's
Object Design Contract. They are expected to FAIL against the stubs.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _root in (REPO_ROOT, os.path.join(REPO_ROOT, "src"), os.path.join(REPO_ROOT, "src", "nebuko", "engine", "lib")):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from answer_footer import (  # noqa: E402
    AnswerFooter,
    FooterStatus,
    StepLinkResolver,
    format_answer_footer,
)
from workflow_state.sub_domains.gate_lock.gate_lock import (  # noqa: E402
    GateLock,
    GateStatus,
)
from workflow_state.sub_domains.history_entry.history_entry import (  # noqa: E402
    HistoryEntry,
)
from workflow_state.sub_domains.thread_record.thread_record import (  # noqa: E402
    ThreadRecord,
    ThreadRecordKind,
)
from workflow_state.sub_domains.turn_outcome.turn_outcome import (  # noqa: E402
    Appreciation,
    HoldKind,
    IncompleteReason,
    TurnOutcome,
    TurnStatus,
)
from workflow_state.workflow_state import WorkflowState  # noqa: E402


def _outcome(
    status: TurnStatus = TurnStatus.COMPLETED,
    appreciation: Appreciation | None = None,
    hold_kind: HoldKind | None = None,
    incomplete_reason=None,
) -> TurnOutcome:
    kwargs: dict = {"status": status}
    if appreciation is not None:
        kwargs["appreciation"] = appreciation
    if hold_kind is not None:
        kwargs["hold_kind"] = hold_kind
    if incomplete_reason is not None:
        kwargs["incomplete_reason"] = incomplete_reason
    return TurnOutcome.model_validate(kwargs)


def _state(**overrides) -> WorkflowState:
    defaults: dict = {"workflow": "spec-tdd", "step": "spec"}
    defaults.update(overrides)
    return WorkflowState.model_validate(defaults)


def _entry(step: str, comment_ids: list[int] | None = None) -> HistoryEntry:
    return HistoryEntry.model_validate(
        {
            "step": step,
            "agent": "architect",
            "comment_ids": comment_ids or [],
            "turn_outcome": {"status": "completed", "appreciation": "approved"},
        }
    )


def _agent_record(comment_id: int, url: str) -> ThreadRecord:
    return ThreadRecord.model_validate(
        {
            "kind": ThreadRecordKind.AGENT_OUTPUT,
            "id": comment_id,
            "url": url,
            "user_login": "nebuko-bot",
        }
    )


class TestFormatAnswerFooterEmptyState(unittest.TestCase):
    def test_gives_empty_string_when_state_is_none(self):
        """Given no state, when formatting the footer, then it is ""."""
        self.assertEqual(format_answer_footer(None), "")

    def test_gives_empty_string_when_workflow_is_empty(self):
        """Given a state with no workflow name, when rendering, then ""."""
        self.assertEqual(format_answer_footer(_state(workflow="")), "")


class TestFooterFourSegmentFormat(unittest.TestCase):
    def test_renders_workflow_steps_status_hint_pipe_separated(self):
        """Given an in-progress spec-tdd run at spec, when rendering,
        then the footer is the four pipe-separated segments with the
        hint omitted for in-progress."""
        footer = format_answer_footer(_state(), steps=["plan", "spec", "implement"])
        self.assertEqual(
            footer, "spec-tdd | plan -> **spec** -> implement | in progress"
        )

    def test_matches_the_issue_example_for_an_armed_gate(self):
        """Given an armed gate on the spec step, when rendering, then the
        footer matches the issue's example line."""
        state = _state(gate=GateLock(step="spec", status=GateStatus.ARMED))
        footer = format_answer_footer(state, steps=["plan", "spec", "implement"])
        self.assertEqual(
            footer,
            "spec-tdd | plan -> **spec** -> implement | gate: need approval"
            " | approve or reject: /nebuko approve|reject gate",
        )

    def test_matches_the_issue_example_for_a_failed_soft_kill(self):
        """Given a soft-kill retry budget exhausted on the spec step, when
        rendering, then the footer matches the issue's second example."""
        state = _state(
            step="spec",
            soft_retry={"step": "spec", "attempts": 3},
            turn_outcome=_outcome(
                TurnStatus.INCOMPLETE,
                incomplete_reason=IncompleteReason.SOFT_TIMEOUT,
            ),
        )
        footer = AnswerFooter().render(state, steps=["plan", "spec", "implement"])
        self.assertIn("| soft-kill failed | retry /nebuko resume or escalate to architect", footer)


class TestStepsSegment(unittest.TestCase):
    def test_completed_step_is_linked_to_its_latest_output_permalink(self):
        """Given a completed plan step with a posted agent answer, when
        rendering steps, then the plan step is a Markdown link to that
        comment's permalink."""
        state = _state(
            history=[_entry("plan", [42])],
        )
        resolver = StepLinkResolver(
            thread=[_agent_record(42, "https://github.com/o/r#issuecomment-42")],
            history=state.history,
        )
        steps = AnswerFooter(resolver).render_steps(state, ["plan", "spec"])
        self.assertIn("[plan](https://github.com/o/r#issuecomment-42)", steps)

    def test_resolver_without_url_falls_back_to_plain_step_name(self):
        """Given a completed step with no resolvable permalink, when
        rendering steps, then the step name stays plain."""
        state = _state(
            history=[_entry("plan", [42])],
        )
        resolver = StepLinkResolver(thread=[], history=state.history)
        steps = AnswerFooter(resolver).render_steps(state, ["plan", "spec"])
        self.assertIn("plan", steps)
        self.assertNotIn("[plan]", steps)

    def test_current_step_is_bold(self):
        """Given the run is on spec, when rendering steps, then spec is
        wrapped in ** markers."""
        steps = AnswerFooter().render_steps(_state(), ["plan", "spec", "implement"])
        self.assertIn("**spec**", steps)

    def test_pending_step_is_plain(self):
        """Given implement has not run, when rendering steps, then it is
        neither bold nor linked."""
        steps = AnswerFooter().render_steps(_state(), ["plan", "spec", "implement"])
        self.assertIn("-> implement", steps)
        self.assertNotIn("**implement**", steps)

    def test_steps_fall_back_to_history_plus_current_step(self):
        """Given no explicit step list, when rendering, then the steps come
        from the history's completed steps plus the current one."""
        state = _state(
            history=[
                _entry("plan"),
            ],
        )
        steps = AnswerFooter().render_steps(state, None)
        self.assertEqual(steps, "plan -> **spec**")


class TestStatusResolution(unittest.TestCase):
    def test_open_circuit_breaker_wins_over_everything(self):
        """Given a tripped breaker and an armed gate, when resolving the
        status, then circuit-breaker-open wins (first match)."""
        state = _state(
            gate=GateLock(step="spec", status=GateStatus.ARMED),
            publish_failed=False,
            turn_outcome=_outcome(TurnStatus.INCOMPLETE, incomplete_reason=IncompleteReason.AGENT_CHECKPOINT),
        )
        # The breaker's open signal is the engine's tripped marker in the
        # resolved guard verdict; the footer reads the state's persisted
        # surface: a tripped breaker leaves the halt in current_context.
        state = state.model_copy(
            update={"current_context": {"circuit_breaker_tripped": True}}
        )
        self.assertIs(AnswerFooter().resolve_status(state), FooterStatus.CIRCUIT_OPEN)

    def test_exhausted_soft_retry_beats_armed_gate(self):
        """Given a spent retry budget and an armed gate, when resolving,
        then soft-kill-failed wins."""
        state = _state(
            step="spec",
            soft_retry={"step": "spec", "attempts": 3},
            gate=GateLock(step="spec", status=GateStatus.ARMED),
            turn_outcome=_outcome(TurnStatus.INCOMPLETE, incomplete_reason=IncompleteReason.SOFT_TIMEOUT),
        )
        self.assertIs(
            AnswerFooter().resolve_status(state), FooterStatus.SOFT_RETRY_EXHAUSTED
        )

    def test_armed_gate_is_gate_status(self):
        """Given an armed root gate lock, when resolving, then gate-armed."""
        state = _state(gate=GateLock(step="spec", status=GateStatus.ARMED))
        self.assertIs(AnswerFooter().resolve_status(state), FooterStatus.GATE_ARMED)

    def test_hold_appreciation_is_awaiting_input(self):
        """Given a completed turn with a hold verdict, when resolving, then
        awaiting-input."""
        state = _state(
            turn_outcome=_outcome(TurnStatus.COMPLETED, Appreciation.HOLD, HoldKind.AWAITING_INPUT),
        )
        self.assertIs(AnswerFooter().resolve_status(state), FooterStatus.AWAITING_INPUT)

    def test_plain_run_is_in_progress(self):
        """Given no stop condition, when resolving, then in progress."""
        self.assertIs(
            AnswerFooter().resolve_status(_state()), FooterStatus.IN_PROGRESS
        )


class TestHints(unittest.TestCase):
    def test_gate_armed_hint_names_the_gate_commands(self):
        footer = AnswerFooter().render_hint(FooterStatus.GATE_ARMED, _state())
        self.assertEqual(footer, "approve or reject: /nebuko approve|reject gate")

    def test_awaiting_input_hint_names_resume(self):
        footer = AnswerFooter().render_hint(FooterStatus.AWAITING_INPUT, _state())
        self.assertEqual(footer, "reply then /nebuko resume")

    def test_soft_retry_exhausted_hint_names_resume_or_escalation(self):
        footer = AnswerFooter().render_hint(FooterStatus.SOFT_RETRY_EXHAUSTED, _state())
        self.assertEqual(footer, "retry /nebuko resume or escalate to architect")

    def test_circuit_open_hint_names_the_update_command(self):
        footer = AnswerFooter().render_hint(FooterStatus.CIRCUIT_OPEN, _state())
        self.assertEqual(footer, "adjust: /nebuko update circuit-breaker --value <n>")

    def test_in_progress_hint_is_omitted_entirely(self):
        """Given in progress, when rendering the footer, then no trailing
        separator or empty hint segment remains."""
        footer = format_answer_footer(_state(), steps=["plan", "spec"])
        self.assertEqual(footer, "spec-tdd | plan -> **spec** | in progress")
        self.assertFalse(footer.endswith("|"))


if __name__ == "__main__":
    unittest.main()
