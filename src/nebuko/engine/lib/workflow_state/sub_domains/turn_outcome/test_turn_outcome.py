"""Unit tests for the factual turn-outcome entity (issue #923)."""

import pytest

from workflow_state.sub_domains.dispatch.dispatch import Dispatch
from workflow_state.sub_domains.turn_outcome.turn_outcome import (
    Appreciation,
    HoldKind,
    IncompleteReason,
    TurnOutcome,
    TurnStatus,
)
from agent_script.features.run_agent.run_outcome import OutcomeStatus


class TestValidators:
    """The cross-field iff rules make only 4 outcomes reachable."""

    def test_completed_requires_appreciation(self):
        with pytest.raises(ValueError, match="appreciation is required"):
            TurnOutcome(status=TurnStatus.COMPLETED)

    def test_incomplete_requires_reason(self):
        with pytest.raises(ValueError, match="incomplete_reason is required"):
            TurnOutcome(status=TurnStatus.INCOMPLETE)

    def test_incomplete_forbids_appreciation(self):
        with pytest.raises(ValueError, match="appreciation is forbidden"):
            TurnOutcome(
                status=TurnStatus.INCOMPLETE,
                appreciation=Appreciation.APPROVED,
                incomplete_reason=IncompleteReason.SOFT_TIMEOUT,
            )

    def test_completed_forbids_incomplete_reason(self):
        with pytest.raises(ValueError, match="incomplete_reason is forbidden"):
            TurnOutcome(
                status=TurnStatus.COMPLETED,
                appreciation=Appreciation.APPROVED,
                incomplete_reason=IncompleteReason.SOFT_TIMEOUT,
            )

    def test_hold_requires_hold_kind(self):
        with pytest.raises(ValueError, match="hold_kind is required"):
            TurnOutcome(
                status=TurnStatus.COMPLETED, appreciation=Appreciation.HOLD
            )

    def test_hold_kind_only_with_hold(self):
        with pytest.raises(ValueError, match="hold_kind is only valid"):
            TurnOutcome(
                status=TurnStatus.COMPLETED,
                appreciation=Appreciation.APPROVED,
                hold_kind=HoldKind.AWAITING_INPUT,
            )

    def test_reachable_grid(self):
        for appr in (Appreciation.APPROVED, Appreciation.REJECTED):
            outcome = TurnOutcome(status=TurnStatus.COMPLETED, appreciation=appr)
            assert outcome.route_key == appr.value
        hold = TurnOutcome(
            status=TurnStatus.COMPLETED,
            appreciation=Appreciation.HOLD,
            hold_kind=HoldKind.AWAITING_APPROVAL,
        )
        assert hold.route_key == "hold"
        incomplete = TurnOutcome.for_incomplete(IncompleteReason.SOFT_TIMEOUT)
        assert incomplete.route_key is None


class TestSerialization:
    def test_mandatory_fields_survive_exclude_defaults(self):
        """The mandatory status/appreciation must not be dropped as defaults."""
        dumped = TurnOutcome(
            status=TurnStatus.COMPLETED, appreciation=Appreciation.REJECTED
        ).model_dump(mode="json", exclude_defaults=True)
        assert dumped["status"] == "completed"
        assert dumped["appreciation"] == "rejected"

    def test_round_trip(self):
        outcome = TurnOutcome(
            status=TurnStatus.COMPLETED,
            appreciation=Appreciation.REJECTED,
            round=2,
        )
        restored = TurnOutcome.model_validate(outcome.model_dump(mode="json"))
        assert restored == outcome


class TestFromAgentPayload:
    def test_success_uses_agent_verdict(self):
        outcome = TurnOutcome.from_agent_payload(
            {"appreciation": "rejected", "round": 2},
            observed=OutcomeStatus.SUCCESS,
        )
        assert outcome.status == TurnStatus.COMPLETED
        assert outcome.appreciation == Appreciation.REJECTED
        assert outcome.round == 2

    def test_engine_observation_wins_on_conflict(self):
        """A timed-out turn never completes, whatever the agent claimed."""
        outcome = TurnOutcome.from_agent_payload(
            {"appreciation": "approved", "note": "looks good"},
            observed=OutcomeStatus.GRACEFULLY_TIMED_OUT,
        )
        assert outcome.status == TurnStatus.INCOMPLETE
        assert outcome.incomplete_reason == IncompleteReason.SOFT_TIMEOUT
        assert outcome.appreciation is None
        assert "claimed appreciation=approved" in outcome.note
        assert "looks good" in outcome.note

    def test_hold_defaults_hold_kind(self):
        outcome = TurnOutcome.from_agent_payload(
            {"appreciation": "hold"}, observed=OutcomeStatus.SUCCESS
        )
        assert outcome.hold_kind == HoldKind.AWAITING_INPUT

    def test_interrupted_maps_to_interrupted(self):
        outcome = TurnOutcome.from_agent_payload(
            {"appreciation": "approved"}, observed=OutcomeStatus.INTERRUPTED
        )
        assert outcome.incomplete_reason == IncompleteReason.INTERRUPTED


class TestFromLegacyDispatch:
    def test_soft_kill_maps_to_incomplete(self):
        outcome = TurnOutcome.from_legacy_dispatch(
            Dispatch(status="soft_kill", reason="timeout")
        )
        assert outcome.status == TurnStatus.INCOMPLETE
        assert outcome.incomplete_reason == IncompleteReason.SOFT_TIMEOUT

    def test_human_gate_hold_maps_to_awaiting_approval(self):
        outcome = TurnOutcome.from_legacy_dispatch(
            Dispatch(status="hold", reason="human_gate:plan")
        )
        assert outcome.appreciation == Appreciation.HOLD
        assert outcome.hold_kind == HoldKind.AWAITING_APPROVAL

    def test_branch_rejected_maps(self):
        outcome = TurnOutcome.from_legacy_dispatch(
            Dispatch(status="advance", branch="rejected", task_hint="dev")
        )
        assert outcome.appreciation == Appreciation.REJECTED
        assert outcome.required_agent == "dev"

    def test_verdictless_legacy_returns_none(self):
        """The captured issue-#923 blob (``{"reason": "round 2"}``) is unmapped."""
        assert (
            TurnOutcome.from_legacy_dispatch(
                Dispatch(status="advance", reason="round 2")
            )
            is None
        )
