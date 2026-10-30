"""Tests for the engine-owned HumanActionNotice sub-domain (issue #795).

The classification matrix and the rendered body are the contract that lets the
engine own every human ping: an agent reports its result and never mentions a
human, so this value object is the single place the ping is decided and built.
"""

import sys
from pathlib import Path

from nebuko.engine.lib.config import HumanActionNotice
from nebuko.engine.lib.workflow_state import Appreciation, HoldKind, IncompleteReason, TurnOutcome


def _outcome(appreciation=None, *, hold_kind=None, note="", incomplete=None):
    if incomplete is not None:
        return TurnOutcome.for_incomplete(incomplete, note=note)
    return TurnOutcome(
        status="completed",
        appreciation=appreciation,
        hold_kind=hold_kind,
        note=note,
    )


def _classify(
    turn_outcome,
    *,
    terminal=False,
    unresolved=False,
    agent_ran=True,
    target="olivier-norture",
    step="plan",
    workflow="default",
    gate_pending=False,
):
    return HumanActionNotice.classify(
        turn_outcome,
        terminal=terminal,
        agent_ran=agent_ran,
        target=target,
        step=step,
        workflow=workflow,
        unresolved=unresolved,
        gate_pending=gate_pending,
    )


class TestClassifyMatrix:
    """Which finished runs owe a human a notification."""

    def test_a_pending_gate_is_an_approval_request(self):
        """The engine-owned gate, not the agent's verdict, blocks the advance
        (issue #1033): an approved run on an armed gate still pings."""
        notice = _classify(_outcome(Appreciation.APPROVED), gate_pending=True)
        assert notice is not None
        assert notice.kind == "awaiting_approval"

    def test_plain_hold_is_an_input_request(self):
        notice = _classify(
            _outcome(Appreciation.HOLD, hold_kind=HoldKind.AWAITING_INPUT)
        )
        assert notice.kind == "awaiting_input"

    def test_hold_defaults_to_an_input_request(self):
        assert (
            _classify(_outcome(Appreciation.HOLD, hold_kind=HoldKind.AWAITING_INPUT)).kind
            == "awaiting_input"
        )

    def test_a_terminal_verdict_is_workflow_complete(self):
        notice = _classify(_outcome(Appreciation.APPROVED), terminal=True)
        assert notice.kind == "workflow_complete"

    def test_a_non_terminal_verdict_never_pings(self):
        assert _classify(_outcome(Appreciation.APPROVED), terminal=False) is None

    def test_an_unresolved_step_is_an_input_request(self):
        notice = _classify(
            TurnOutcome.for_incomplete(IncompleteReason.NO_OUTCOME, note="round 2"),
            unresolved=True,
        )
        assert notice.kind == "awaiting_input"

    def test_an_incomplete_turn_never_pings(self):
        assert _classify(TurnOutcome.for_incomplete(IncompleteReason.SOFT_TIMEOUT)) is None

    def test_a_guard_or_terminal_skip_never_pings(self):
        # agent_ran=False means nothing was produced for a human to act on.
        assert _classify(_outcome(Appreciation.APPROVED), terminal=True, agent_ran=False) is None

    def test_an_unresolvable_target_never_pings(self):
        assert (
            _classify(_outcome(Appreciation.APPROVED), target="", gate_pending=True)
            is None
        )

    def test_records_the_stop_fields_verbatim(self):
        notice = _classify(
            _outcome(Appreciation.HOLD, hold_kind=HoldKind.AWAITING_INPUT, note="awaiting"),
            step="plan",
            workflow="default",
        )
        assert (notice.step, notice.workflow, notice.reason) == (
            "plan",
            "default",
            "awaiting",
        )


class TestRender:
    """The rendered body is the single canonical human ping."""

    def test_render_is_headed_by_the_bot_and_human_action_markers(self):
        lines = _classify(_outcome(Appreciation.APPROVED, note="human_gate:plan"), gate_pending=True).render().splitlines()
        assert lines[0] == "<!-- bot-workflow-terminal -->"
        assert lines[1] == "<!-- bot-human-action -->"

    def test_render_pings_the_target_exactly_once(self):
        body = _classify(_outcome(Appreciation.APPROVED, note="human_gate:plan"), gate_pending=True).render()
        assert body.count("@olivier-norture") == 1
        assert "@olivier-norture workflow **default**" in body

    def test_approval_body_names_the_step_and_the_resume_command(self):
        body = _classify(_outcome(Appreciation.APPROVED, note="human_gate:plan"), step="plan", gate_pending=True).render()
        assert "waiting for your approval" in body
        assert "step `plan`" in body
        assert "/nebuko resume" in body

    def test_input_body_asks_for_feedback(self):
        body = _classify(_outcome(Appreciation.HOLD, hold_kind=HoldKind.AWAITING_INPUT, note="await something"), step="context").render()
        assert "paused at step `context`" in body
        assert "/nebuko resume --feedback \"<your feedback>\"" in body

    def test_complete_body_announces_the_finish(self):
        body = _classify(_outcome(Appreciation.APPROVED), terminal=True, step="review").render()
        assert "has finished at step `review`" in body

    def test_reason_is_appended_when_present(self):
        body = _classify(_outcome(Appreciation.APPROVED, note="human_gate:plan"), gate_pending=True).render()
        assert "_human_gate:plan_" in body

    def test_no_reason_line_when_absent(self):
        body = _classify(_outcome(Appreciation.APPROVED), terminal=True).render()
        assert not body.rstrip().endswith("_")

    def test_reason_cannot_smuggle_a_live_human_ping(self):
        """The agent-authored reason is neutralized: only ``target`` pings (#795)."""
        body = _classify(_outcome(Appreciation.HOLD, hold_kind=HoldKind.AWAITING_INPUT, note="blocked, cc @someone")).render()
        assert "@\u200bsomeone" in body
        assert "@someone" not in body
        # The engine's own ping is the single live mention in the body.
        assert body.count("@olivier-norture") == 1

    def test_reason_mention_in_code_span_stays_readable(self):
        """The neutralizer preserves code spans (GitHub does not notify there)."""
        body = _classify(_outcome(Appreciation.HOLD, hold_kind=HoldKind.AWAITING_INPUT, note="see `@dataclass` cc @someone")).render()
        assert "`@dataclass`" in body
        assert "@\u200bsomeone" in body


class TestValueObject:
    """The notice is a frozen payload, like AgentStartNotice."""

    def test_is_immutable(self):
        notice = _classify(_outcome(Appreciation.APPROVED, note="human_gate:plan"), gate_pending=True)
        try:
            notice.kind = "workflow_complete"
        except Exception as exc:  # pydantic raises ValidationError on frozen models
            assert "frozen" in str(exc).lower()
        else:  # pragma: no cover - frozen models never reach this branch
            raise AssertionError("HumanActionNotice should be immutable")

    def test_round_trips_through_model_dump(self):
        notice = _classify(_outcome(Appreciation.APPROVED, note="human_gate:plan"), gate_pending=True)
        assert HumanActionNotice.model_validate(notice.model_dump()) == notice
