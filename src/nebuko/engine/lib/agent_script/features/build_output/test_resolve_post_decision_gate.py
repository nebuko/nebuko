"""Red tests: a rejected-with-feedback gate re-run must re-arm a fresh gate.

Replays the issue's ``workflow_json`` scenario: a `human_gate` step (`plan`)
is rejected with feedback, the gated step re-runs and completes — the
persisted gate must be ARMED again (never `released`), so the workflow
cannot advance to `implement` without a new `/nebuko approve gate`.
"""

import unittest
from datetime import datetime, timezone

from build_output import resolve_post_decision_gate
from nebuko.engine.lib.config import StepRef
from nebuko.engine.lib.workflow_state import (
    GateDecision,
    GateLock,
    GateStatus,
)


def _decision(status: str, feedback: str = "", target: str = "plan") -> GateDecision:
    return GateDecision(
        status=status,
        workflow="default",
        target_step=target,
        decided_by="olivier-norture",
        decided_at=datetime(2026, 10, 26, 20, 15, 39, tzinfo=timezone.utc),
        feedback=feedback,
        comment_id=6426299459,
        position=[StepRef(workflow="default", step=target)],
    )


class TestResolvePostDecisionGate(unittest.TestCase):
    """resolve_post_decision_gate() derives the final lock from the decision."""

    def test_rejected_with_feedback_rerun_completion_arms_fresh_gate(self):
        gate = resolve_post_decision_gate(
            GateLock(step="plan", status=GateStatus.ARMED),
            "plan",
            _decision("rejected", feedback="Use Posthog"),
            gate_released=True,
        )
        self.assertIsNotNone(gate)
        self.assertEqual(gate.status, GateStatus.ARMED)
        self.assertEqual(gate.step, "plan")
        self.assertEqual(gate.consult_rounds, 0)

    def test_approved_decision_releases_gate(self):
        gate = resolve_post_decision_gate(
            GateLock(step="plan", status=GateStatus.ARMED),
            "plan",
            _decision("approved"),
            gate_released=True,
        )
        self.assertEqual(gate.status, GateStatus.RELEASED)

    def test_no_decision_leaves_gate_untouched(self):
        armed = GateLock(step="plan", status=GateStatus.ARMED)
        self.assertIs(resolve_post_decision_gate(armed, "plan", None, True), armed)


if __name__ == "__main__":
    unittest.main()
