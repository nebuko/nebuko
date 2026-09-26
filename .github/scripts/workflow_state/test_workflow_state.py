import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workflow_state import WorkflowState, HistoryEntry, StateMetrics

class TestWorkflowStateSchema(unittest.TestCase):
    """Test WorkflowState Pydantic schema validation."""

    def test_minimal_state(self):
        state = WorkflowState(workflow="test", step="analyze", agent="genius")
        self.assertEqual(state.version, 1)
        self.assertEqual(state.phase, "")
        self.assertEqual(state.history, [])
        self.assertEqual(state.pinned_comment_ids, [])
        self.assertIsNone(state.parent_workflow)

    def test_full_state(self):
        state = WorkflowState(
            workflow="default",
            step="implement",
            agent="dev",
            version=5,
            phase="execution",
            history=[HistoryEntry(step="analyze", agent="genius", summary="Analyzed issue")],
            pinned_comment_ids=[12345],
            current_context={"task": "implement feature"},
            metrics=StateMetrics(input_tokens=1000, output_tokens=500),
            parent_workflow="spec-driven-design",
            parent_workflow_step="stub_phase",
        )
        self.assertEqual(state.version, 5)
        self.assertEqual(len(state.history), 1)
        self.assertIn(12345, state.pinned_comment_ids)

    def test_defaults_for_optional_fields(self):
        """Step, agent, phase default to empty string for backward compat."""
        state = WorkflowState(workflow="test", agent="genius")
        self.assertEqual(state.step, "")
        self.assertEqual(state.phase, "")
        self.assertEqual(state.version, 1)

    def test_to_json_round_trip_preserves_position(self):
        """Populated v3 position + history position survive to_json ->
        re-validate (issue #638): exclude_defaults must NOT collapse a
        non-empty refs list — the history[-1].position == position
        invariant depends on this seam."""
        import json
        from config import StepRef
        from workflow_state.workflow_state import to_json

        refs = [
            StepRef(workflow="plan-orchestration", step="sub_impl"),
            StepRef(workflow="dev-review-loop", step="implement"),
        ]
        state = WorkflowState(
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            position=refs,
            history=[
                HistoryEntry(
                    step="implement", agent="dev",
                    summary="impl", position=refs,
                )
            ],
        )
        serialized = to_json(state)
        # Byte-identical round trip: position + entry position survive.
        back = WorkflowState.model_validate(json.loads(serialized))
        self.assertEqual(back.position, refs)
        self.assertEqual(back.history[-1].position, refs)

    def test_v2_position_migration_does_not_mutate_input_dict(self):
        """The position migration validator must not destroy the caller's
        raw dict (issue #638, review fix round 1): a freshly json.load-ed
        state file must stay json.dumps-able after validation."""
        import json

        raw = {
            "workflow": "dev-review-loop",
            "step": "implement",
            "agent": "dev",
            "position": {
                "version": 2,
                "workflow": "dev-review-loop",
                "step": "implement",
                "workflow_name": "plan-orchestration",
                "step_name": "sub_impl",
                "step": {
                    "workflow_name": "dev-review-loop",
                    "step_name": "implement",
                },
            },
        }
        original = json.dumps(raw)
        state = WorkflowState.model_validate(raw)
        # Migrated to the flat v3 refs (outermost-first chain walk).
        self.assertEqual(
            [(r.workflow, r.step) for r in state.position],
            [("plan-orchestration", "sub_impl"),
             ("dev-review-loop", "implement")],
        )
        # Caller's dict untouched — still serializes to the same payload.
        self.assertEqual(json.dumps(raw), original)


class TestStateMetricsCost(unittest.TestCase):
    """Cost is carried through the compact workflow-state metrics."""

    def test_cost_defaults_to_zero(self):
        self.assertEqual(StateMetrics().cost, 0.0)

    def test_cost_extracted_from_full_metrics_dict(self):
        metrics = StateMetrics.from_full_metrics_dict(
            {"input_tokens": 10, "output_tokens": 5, "cost": 0.1234}
        )
        self.assertEqual(metrics.cost, 0.1234)

    def test_cost_defaults_when_absent(self):
        """Legacy metric dicts without 'cost' still load (backward compat)."""
        metrics = StateMetrics.from_full_metrics_dict({"input_tokens": 10})
        self.assertEqual(metrics.cost, 0.0)
