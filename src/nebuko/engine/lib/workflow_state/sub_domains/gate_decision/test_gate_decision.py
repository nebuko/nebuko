"""Tests for the engine-owned GateDecision model (issue #648)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nebuko.engine.lib.workflow_state.sub_domains.gate_decision import GateDecision

from nebuko.engine.lib.config import StepRef


DECIDED_AT = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _position() -> list[StepRef]:
    return [StepRef(workflow="plan-orchestration", step="sub_impl")]


def _decision(**overrides) -> GateDecision:
    payload = {
        "status": "approved",
        "workflow": "default",
        "target_step": "plan",
        "decided_by": "olivier-norture",
        "decided_at": DECIDED_AT,
    }
    payload.update(overrides)
    return GateDecision(**payload)


class TestGateDecisionModel:
    def test_defaults(self):
        decision = _decision()
        assert decision.feedback == ""
        assert decision.comment_id is None
        assert decision.position == []

    def test_full_payload(self):
        decision = _decision(
            status="rejected",
            feedback="please revise the X section",
            comment_id=809,
            position=_position(),
        )
        assert decision.status == "rejected"
        assert decision.feedback == "please revise the X section"
        assert decision.comment_id == 809
        assert decision.position == _position()

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            _decision(status="maybe")

    def test_extra_key_rejected(self):
        with pytest.raises(ValidationError):
            _decision(unexpected="value")

    def test_model_is_frozen(self):
        decision = _decision()
        with pytest.raises(ValidationError):
            decision.status = "rejected"

    def test_json_round_trip_preserves_all_fields(self):
        decision = _decision(
            status="rejected",
            feedback="nope",
            comment_id=42,
            position=_position(),
        )
        assert GateDecision.model_validate_json(decision.model_dump_json()) == decision


class TestGateDecisionIsFor:
    def test_matches_identical_triple(self):
        decision = _decision(workflow="default", target_step="plan", position=_position())
        assert decision.is_for("default", "plan", _position()) is True

    def test_mismatch_on_workflow(self):
        decision = _decision(workflow="default", target_step="plan", position=_position())
        assert decision.is_for("plan-orchestration", "plan", _position()) is False

    def test_mismatch_on_step(self):
        decision = _decision(workflow="default", target_step="plan", position=_position())
        assert decision.is_for("default", "implement", _position()) is False

    def test_mismatch_on_position(self):
        decision = _decision(workflow="default", target_step="plan", position=_position())
        assert decision.is_for("default", "plan", []) is False
