import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from workflow_state import WorkflowState, ParentRef, build_state, go_next

class TestBuildState(unittest.TestCase):
    """Test build_state() pure state-transition function."""

    def test_initial_state(self):
        state = build_state(None, workflow="default", step="analyze", agent="genius")
        self.assertEqual(state.version, 1)
        self.assertEqual(state.workflow, "default")
        self.assertEqual(state.step, "analyze")
        self.assertEqual(state.agent, "genius")
        self.assertEqual(len(state.history), 1)

    def test_append_to_existing(self):
        prev = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
            summary="Analyzed request",
        )
        next_state = build_state(
            prev,
            workflow="default",
            step="context",
            agent="ctx",
            summary="Collected files",
        )
        self.assertEqual(next_state.version, 2)
        self.assertEqual(len(next_state.history), 2)
        self.assertEqual(next_state.history[0].summary, "Analyzed request")
        self.assertEqual(next_state.history[1].summary, "Collected files")

    def test_phase_derivation(self):
        state = build_state(
            None,
            workflow="default",
            step="implement",
            agent="dev",
            phase="execution",
        )
        self.assertEqual(state.phase, "execution")

    def test_pinning(self):
        state = build_state(
            None,
            workflow="default",
            step="plan",
            agent="genius",
            summary="Architecture proposal",
            pin=True,
        )
        self.assertTrue(state.history[0].pin)

    def test_result_field(self):
        state = build_state(
            None,
            workflow="default",
            step="dev",
            agent="dev",
            summary="Implementation done",
            result="All tests passed",
        )
        self.assertEqual(state.history[0].result, "All tests passed")

    def test_result_defaults_to_none(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
        )
        self.assertIsNone(state.history[0].result)

    def test_metrics_included(self):
        metrics_dict = {"input_tokens": 5000, "output_tokens": 2000}
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
            metrics_dict=metrics_dict,
        )
        self.assertIsNotNone(state.metrics)
        self.assertEqual(state.metrics.input_tokens, 5000)

    def test_resolution_included(self):
        from workflow_state import StepResolution
        resolution = StepResolution(
            role_name="dev",
            selected_workflow="spec-driven-design",
            selected_step="implement",
            matched_rule="workflow:spec-driven-design/step:implement",
            timestamp="2024-01-01T00:00:00+00:00",
        )
        state = build_state(
            None,
            workflow="spec-driven-design",
            step="implement",
            agent="dev",
            resolution=resolution,
        )
        self.assertIsNotNone(state.resolution)
        self.assertEqual(state.resolution.role_name, "dev")
        self.assertEqual(state.resolution.matched_rule, "workflow:spec-driven-design/step:implement")

    def test_resolution_defaults_to_none(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
        )
        self.assertIsNone(state.resolution)

    def test_inject_context_default_true(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
        )
        self.assertTrue(state.history[0].inject_context)

    def test_inject_context_explicit_false(self):
        state = build_state(
            None,
            workflow="default",
            step="context",
            agent="ctx",
            inject_context=False,
        )
        self.assertFalse(state.history[0].inject_context)

    def test_inject_context_explicit_true(self):
        state = build_state(
            None,
            workflow="default",
            step="analyze",
            agent="genius",
            inject_context=True,
        )
        self.assertTrue(state.history[0].inject_context)

    def test_inject_context_persists_across_entries(self):
        prev = build_state(
            None,
            workflow="default",
            step="context",
            agent="ctx",
            inject_context=False,
        )
        next_state = build_state(
            prev,
            workflow="default",
            step="implement",
            agent="dev",
            inject_context=True,
        )
        self.assertFalse(next_state.history[0].inject_context)
        self.assertTrue(next_state.history[1].inject_context)


class TestBuildStateScaffoldBranch(unittest.TestCase):
    """scaffold_branch persistence rules (issue #604, stub)."""

    def test_initial_state_has_no_scaffold_branch(self):
        state = build_state(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="genius",
        )
        self.assertIsNone(state.scaffold_branch)

    def test_build_state_sets_scaffold_branch(self):
        state = build_state(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="genius",
            summary="Scaffolded plan",
            scaffold_branch="feature/issue-604-scaffold-branch-fetch",
        )
        self.assertEqual(
            state.scaffold_branch, "feature/issue-604-scaffold-branch-fetch"
        )

    def test_scaffold_branch_persists_when_not_repeated(self):
        prev = build_state(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="genius",
            scaffold_branch="feature/issue-604-scaffold-branch-fetch",
        )
        next_state = build_state(
            prev,
            workflow="plan-orchestration",
            step="sub_impl",
            agent="dev",
        )
        self.assertEqual(
            next_state.scaffold_branch, "feature/issue-604-scaffold-branch-fetch"
        )

    def test_scaffold_branch_last_write_wins_on_rescaffold(self):
        prev = build_state(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="genius",
            scaffold_branch="feature/issue-604-old-scaffold",
        )
        next_state = build_state(
            prev,
            workflow="plan-orchestration",
            step="plan",
            agent="genius",
            scaffold_branch="feature/issue-604-new-scaffold",
        )
        self.assertEqual(
            next_state.scaffold_branch, "feature/issue-604-new-scaffold"
        )

    def test_go_next_threads_scaffold_branch(self):
        state = go_next(
            None,
            workflow="plan-orchestration",
            step="plan",
            agent="genius",
            scaffold_branch="feature/issue-604-scaffold-branch-fetch",
        )
        self.assertEqual(
            state.scaffold_branch, "feature/issue-604-scaffold-branch-fetch"
        )

    def test_backward_compat_old_state_json_without_field(self):
        """A state block written before #604 (no scaffold_branch key) loads cleanly."""
        data = {
            "version": 3,
            "workflow": "plan-orchestration",
            "step": "plan",
            "agent": "genius",
            "history": [{"step": "analyze", "agent": "genius", "summary": "ok"}],
        }
        state = WorkflowState.model_validate(data)
        self.assertIsNone(state.scaffold_branch)

class TestBuildStateParent(unittest.TestCase):
    """parent block persistence rules (issue #613, stub)."""

    def test_initial_state_has_no_parent(self):
        state = build_state(
            None,
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
        )
        self.assertIsNone(state.parent)
        self.assertIsNone(state.parent_workflow)
        self.assertIsNone(state.parent_workflow_step)

    def test_build_state_sets_parent_block(self):
        state = build_state(
            None,
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            parent=ParentRef(
                workflow="plan-orchestration",
                step="sub_impl",
                issue_number=589,
                plan_step_index=2,
                plan_total_steps=6,
            ),
        )
        self.assertIsNotNone(state.parent)
        self.assertEqual(state.parent.workflow, "plan-orchestration")
        self.assertEqual(state.parent.step, "sub_impl")
        self.assertEqual(state.parent.issue_number, 589)
        self.assertEqual(state.parent.plan_step_index, 2)
        self.assertEqual(state.parent.plan_total_steps, 6)

    def test_parent_persists_when_not_repeated(self):
        prev = build_state(
            None,
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            parent=ParentRef(
                workflow="plan-orchestration",
                step="sub_impl",
                issue_number=589,
                plan_step_index=2,
                plan_total_steps=6,
            ),
        )
        next_state = build_state(
            prev,
            workflow="dev-review-loop",
            step="review",
            agent="review",
        )
        self.assertIsNotNone(next_state.parent)
        self.assertEqual(next_state.parent.workflow, "plan-orchestration")
        self.assertEqual(next_state.parent.plan_step_index, 2)

    def test_parent_last_write_wins_on_override(self):
        prev = build_state(
            None,
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            parent=ParentRef(workflow="plan-orchestration", step="sub_impl"),
        )
        next_state = build_state(
            prev,
            workflow="dev-review-loop",
            step="review",
            agent="review",
            parent=ParentRef(
                workflow="plan-orchestration",
                step="final_fix",
                plan_step_index=3,
            ),
        )
        self.assertEqual(next_state.parent.step, "final_fix")
        self.assertEqual(next_state.parent.plan_step_index, 3)

    def test_go_next_threads_parent(self):
        state = go_next(
            None,
            workflow="dev-review-loop",
            step="implement",
            agent="dev",
            parent=ParentRef(
                workflow="plan-orchestration",
                step="sub_impl",
                issue_number=589,
                plan_step_index=2,
                plan_total_steps=6,
            ),
        )
        self.assertIsNotNone(state.parent)
        self.assertEqual(state.parent.issue_number, 589)

    def test_backward_compat_old_state_json_without_parent(self):
        """A state block written before #613 (no parent key) loads cleanly."""
        data = {
            "version": 3,
            "workflow": "dev-review-loop",
            "step": "implement",
            "agent": "dev",
            "history": [{"step": "implement", "agent": "dev", "summary": "ok"}],
        }
        state = WorkflowState.model_validate(data)
        self.assertIsNone(state.parent)

    def test_legacy_flat_parent_fields_normalize_to_parent_block(self):
        """Pre-#613 flat parent fields normalize into the parent block on read."""
        data = {
            "version": 2,
            "workflow": "dev-review-loop",
            "step": "implement",
            "agent": "dev",
            "parent_workflow": "plan-orchestration",
            "parent_workflow_step": "sub_impl",
        }
        state = WorkflowState.model_validate(data)
        self.assertIsNotNone(state.parent)
        self.assertEqual(state.parent.workflow, "plan-orchestration")
        self.assertEqual(state.parent.step, "sub_impl")

    def test_explicit_parent_block_wins_over_legacy_flat(self):
        """An explicit parent block is not clobbered by the legacy aliases."""
        data = {
            "version": 3,
            "workflow": "dev-review-loop",
            "step": "implement",
            "agent": "dev",
            "parent_workflow": "stale-alias",
            "parent_workflow_step": "stale-alias-step",
            "parent": {
                "workflow": "plan-orchestration",
                "step": "sub_impl",
                "issue_number": 589,
                "plan_step_index": 2,
            },
        }
        state = WorkflowState.model_validate(data)
        self.assertEqual(state.parent.workflow, "plan-orchestration")
        self.assertEqual(state.parent.step, "sub_impl")
        self.assertEqual(state.parent.issue_number, 589)
