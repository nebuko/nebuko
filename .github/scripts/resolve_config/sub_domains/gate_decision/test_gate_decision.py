"""Tests for the GateDecision domain entity and its WorkflowState integration (issue #648)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.sub_domains.gate_decision.gate_decision import GateDecision  # noqa: E402
from workflow_state import WorkflowState  # noqa: E402


class TestGateDecision:
    def test_approved(self):
        gate = GateDecision(
            decision="approved",
            gate_step="stub_approval",
            approver="alice",
            decided_at="2026-01-01T00:00:00Z",
        )
        assert gate.decision == "approved"
        assert gate.gate_step == "stub_approval"
        assert gate.approver == "alice"
        assert gate.reason is None

    def test_rejected_with_reason(self):
        gate = GateDecision(
            decision="rejected",
            gate_step="stub_approval",
            approver="bob",
            decided_at="2026-01-01T00:00:00Z",
            reason="needs more tests",
        )
        assert gate.decision == "rejected"
        assert gate.reason == "needs more tests"

    def test_invalid_decision_rejected(self):
        try:
            GateDecision(
                decision="maybe",
                gate_step="s",
                approver="a",
                decided_at="t",
            )
            assert False, "invalid decision should be rejected"
        except Exception:
            pass


class TestWorkflowStateGate:
    def test_roundtrip_preserves_gate(self):
        gate = GateDecision(
            decision="approved",
            gate_step="stub_approval",
            approver="alice",
            decided_at="2026-01-01T00:00:00Z",
            reason="lgtm",
        )
        state = WorkflowState(gate=gate, pending_gate_step="stub_approval")
        restored = WorkflowState.model_validate_json(state.model_dump_json())
        assert restored.gate == gate
        assert restored.pending_gate_step == "stub_approval"

    def test_legacy_state_without_gate_loads(self):
        state = WorkflowState.model_validate(
            {"version": 1, "workflow": "w", "step": "s"}
        )
        assert state.gate is None
        assert state.pending_gate_step is None
