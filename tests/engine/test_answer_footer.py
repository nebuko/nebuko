"""Red tests for the agent answer footer (#1159).

One test per contract line of the architect plan: the four-segment grammar,
per-step linking/bolding, the five statuses with their resolution order and
fixed hints, the module seam, and the publish-seam wiring.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _root in (
    os.path.join(REPO_ROOT, "src"),
    os.path.join(REPO_ROOT, "src", "nebuko", "engine", "lib"),
):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from answer_footer import (  # noqa: E402
    AnswerFooter,
    FooterStatus,
    StepLinkResolver,
    format_answer_footer,
)
from workflow_state.sub_domains.turn_outcome.turn_outcome import (  # noqa: E402
    Appreciation,
    HoldKind,
    TurnOutcome,
    TurnStatus,
)
from workflow_state.workflow_state import WorkflowState  # noqa: E402


def _state(**overrides) -> WorkflowState:
    base = dict(workflow="spec-tdd", step="spec", history=[], thread=[])
    base.update(overrides)
    return WorkflowState(**base)


from workflow_state.workflow_state import CircuitBreakerState  # noqa: E402


COMPLETED_OUTCOME = TurnOutcome(status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED)


class TestRenderGrammar:
    def test_empty_state_renders_empty_string(self):
        """Given a state with an empty workflow name, when rendered, the footer is ''."""
        assert AnswerFooter().render(_state(workflow="")) == ""

    def test_four_pipe_separated_segments_in_contract_order(self):
        """Given a plain in-progress state, the footer is workflow | steps | status | hint."""
        footer = AnswerFooter().render(_state())
        parts = footer.split(" | ")
        assert parts[0] == "spec-tdd"
        assert parts[1] == "**spec**"
        assert parts[2] == "in progress"
        assert len(parts) == 3  # in-progress omits the hint segment entirely

    def test_hint_segment_omitted_when_in_progress(self):
        """An in-progress run carries no hint segment, not an empty one."""
        footer = AnswerFooter().render(_state())
        assert footer.endswith("in progress")
        assert " |  |" not in footer


class TestStepsSegment:
    def test_full_step_list_rendered_in_order_with_current_bold(self):
        """Given an explicit step list, the current step is bold, others plain."""
        steps = ["plan", "spec", "implement"]
        footer = AnswerFooter().render(_state(step="spec"), steps)
        assert footer.split(" | ")[1] == "plan -> **spec** -> implement"

    def test_pending_step_without_list_falls_back_to_current_only(self):
        """Without an explicit list, the history/current step is used."""
        footer = AnswerFooter().render(_state(step="review"))
        assert footer.split(" | ")[1] == "**review**"


class TestStepLinkResolver:
    def _thread_entry(self, step, comment_id, url, deleted=False):
        from workflow_state.sub_domains.thread_record.thread_record import (
            ThreadRecord,
        )
        from workflow_state.sub_domains.thread_record.thread_record import (
            ThreadRecordKind,
        )
        from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry

        entry = HistoryEntry(
            step=step,
            agent="dev",
            turn_outcome=COMPLETED_OUTCOME,
            comment_ids=[comment_id],
        )
        record = ThreadRecord(
            kind=ThreadRecordKind.AGENT_OUTPUT,
            id=comment_id,
            url=url,
            deleted=deleted,
        )
        return entry, record

    def test_completed_step_links_to_latest_agent_output_permalink(self):
        """A completed step whose output comment exists renders as a Markdown link."""
        entry, record = self._thread_entry("plan", 111, "https://github.com/o/r/issues/1#issuecomment-111")
        state = _state(history=[entry], thread=[record])
        footer = AnswerFooter().render(state, ["plan", "spec"])
        assert (
            "[plan](https://github.com/o/r/issues/1#issuecomment-111)"
            in footer.split(" | ")[1]
        )

    def test_resolver_returns_empty_for_step_without_output(self):
        """A step with no posted output resolves to '' and renders plain."""
        resolver = StepLinkResolver()
        assert resolver.url_for("plan") == ""

    def test_deleted_or_url_less_records_do_not_link(self):
        """Deleted records never resolve to a permalink."""
        entry, record = self._thread_entry("plan", 111, "https://x", deleted=True)
        resolver = StepLinkResolver(thread=[record], history=[entry])
        assert resolver.url_for("plan") == ""


class TestStatusResolution:
    def test_gate_armed_status_and_hint(self):
        from workflow_state.sub_domains.gate_lock.gate_lock import GateLock
        state = _state(gate=GateLock(step="spec", status="armed"))
        assert AnswerFooter().resolve_status(state) is FooterStatus.GATE_ARMED
        assert AnswerFooter().render(state) == (
            "spec-tdd | **spec** | gate: need approval | "
            "approve or reject: /nebuko approve|reject gate"
        )

    def test_awaiting_input_hold_status_and_hint(self):
        state = _state(
            turn_outcome=TurnOutcome(
                status=TurnStatus.COMPLETED,
                appreciation=Appreciation.HOLD,
                hold_kind=HoldKind.AWAITING_INPUT,
            )
        )
        assert AnswerFooter().resolve_status(state) is FooterStatus.AWAITING_INPUT
        assert "reply then /nebuko resume" in AnswerFooter().render(state)

    def test_soft_retry_exhausted_status_and_hint(self):
        from workflow_state import workflow_state as ws
        state = _state(
            soft_retry=ws.SoftRetryState(step="spec", attempts=3)
        )
        # Exhaustion = the step's consumed attempts reached the default budget.
        assert AnswerFooter().resolve_status(state) is FooterStatus.SOFT_RETRY_EXHAUSTED
        assert "retry /nebuko resume or escalate to architect" in AnswerFooter().render(state)

    def test_circuit_open_status_and_hint(self):
        # A zero per-issue call override is the persisted signal that the
        # breaker is open for this issue (issue #839).
        state = _state(circuit_breaker=CircuitBreakerState(limit=0))
        assert AnswerFooter().resolve_status(state) is FooterStatus.CIRCUIT_OPEN
        assert "adjust: /nebuko update circuit-breaker" in AnswerFooter().render(state)


class TestStatusPriorityOrder:
    def test_circuit_open_wins_over_gate(self):
        from workflow_state.sub_domains.gate_lock.gate_lock import GateLock
        from workflow_state.workflow_state import CircuitBreakerState

        state = _state(
            gate=GateLock(step="s", status="armed"),
            circuit_breaker=CircuitBreakerState(limit=0),
        )
        assert AnswerFooter().resolve_status(state) is FooterStatus.CIRCUIT_OPEN


class TestModuleSeam:
    def test_format_answer_footer_none_state_renders_empty(self):
        assert format_answer_footer(None) == ""

    def test_format_answer_footer_delegates_to_answer_footer(self):
        state = _state()
        assert format_answer_footer(state) == AnswerFooter().render(state)
