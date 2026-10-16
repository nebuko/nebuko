"""Unit tests for the factual turn-outcome entity (issue #923)."""

import pytest

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

    def test_agent_checkpoint_maps_to_agent_checkpoint(self):
        """A fold (issue #965) is an incomplete checkpoint, never a completion."""
        outcome = TurnOutcome.from_agent_payload(
            {"appreciation": "approved", "note": "fresh start"},
            observed=OutcomeStatus.AGENT_CHECKPOINT,
        )
        assert outcome.status == TurnStatus.INCOMPLETE
        assert outcome.incomplete_reason == IncompleteReason.AGENT_CHECKPOINT
        assert outcome.appreciation is None


class TestFromAgentPayloadRejectsBadVerdicts:
    """A payload that carries no usable verdict must not become an approval.

    The verdict is the routing fact (issue #964); defaulting a missing or
    misspelled appreciation to ``approved`` would silently advance a step the
    agent never approved.
    """

    def test_invalid_appreciation_is_rejected(self):
        with pytest.raises(ValueError, match="appreciation"):
            TurnOutcome.from_agent_payload(
                {"appreciation": "bogus"}, observed=OutcomeStatus.SUCCESS
            )

    def test_explicitly_empty_appreciation_means_no_verdict(self):
        """``""``/``None`` is a missing verdict, not a typo.

        The engine's own observed-status call sites (soft kill, crash, no
        action) put an explicit ``None`` in the payload when they name no
        verdict; only a *present but unusable* string is an error.
        """
        for empty in ("", None):
            outcome = TurnOutcome.from_agent_payload(
                {"appreciation": empty}, observed=OutcomeStatus.SUCCESS
            )
            assert outcome.appreciation == Appreciation.APPROVED

    def test_explicitly_empty_appreciation_stays_verdictless_on_a_failure(self):
        outcome = TurnOutcome.from_agent_payload(
            {"appreciation": None}, observed=OutcomeStatus.FAILURE
        )
        assert outcome.status == TurnStatus.INCOMPLETE
        assert outcome.appreciation is None

    def test_invalid_appreciation_is_still_flagged_on_an_incomplete_turn(self):
        with pytest.raises(ValueError, match="appreciation"):
            TurnOutcome.from_agent_payload(
                {"appreciation": "bogus"}, observed=OutcomeStatus.GRACEFULLY_TIMED_OUT
            )

    def test_a_payload_without_the_key_is_still_honoured(self):
        """The engine's own internal call sites build payloads by hand."""
        outcome = TurnOutcome.from_agent_payload({}, observed=OutcomeStatus.SUCCESS)
        assert outcome.appreciation == Appreciation.APPROVED

    def test_an_engine_observed_failure_needs_no_verdict(self):
        """The engine's observation is the fact, so no verdict is required."""
        outcome = TurnOutcome.from_agent_payload(
            {}, observed=OutcomeStatus.GRACEFULLY_TIMED_OUT
        )
        assert outcome.incomplete_reason == IncompleteReason.SOFT_TIMEOUT
