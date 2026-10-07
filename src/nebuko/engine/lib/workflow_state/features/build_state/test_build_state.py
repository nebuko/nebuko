import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from workflow_state import WorkflowState, build_state, go_next, Dispatch
from config import POSITION_STATE_VERSION

class TestBuildState(unittest.TestCase):
    """Test build_state() pure state-transition function."""

    def test_initial_state(self):
        state = build_state(None, workflow="default", step="analyze", agent="genius")
        # issue #646: position is universal → every built state is at least
        # the v3 shape.
        self.assertEqual(state.version, POSITION_STATE_VERSION)
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
        self.assertEqual(next_state.version, POSITION_STATE_VERSION + 1)
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

    def test_pin_adds_comment_ids_to_pinned_comment_ids(self):
        state = build_state(
            None,
            workflow="default",
            step="plan",
            agent="genius",
            summary="Architecture proposal",
            pin=True,
            comment_ids=[101, 102],
        )
        self.assertTrue(state.history[0].pin)
        self.assertEqual(state.pinned_comment_ids, [101, 102])

    def test_unpinned_does_not_add_to_pinned_comment_ids(self):
        state = build_state(
            None,
            workflow="default",
            step="dev",
            agent="dev",
            summary="Implementation",
            pin=False,
            comment_ids=[201],
        )
        self.assertEqual(state.pinned_comment_ids, [])

    def test_pinned_comment_ids_persist_and_deduplicate(self):
        prev = build_state(
            None,
            workflow="default",
            step="plan",
            agent="genius",
            summary="Plan",
            pin=True,
            comment_ids=[301],
        )
        nxt = build_state(
            prev,
            workflow="default",
            step="dev",
            agent="dev",
            summary="Impl",
            pin=True,
            comment_ids=[301, 302],
        )
        self.assertEqual(nxt.pinned_comment_ids, [301, 302])

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
        # Metrics live on the history entry, not at the root (issue #792).
        self.assertIsNotNone(state.history[-1].metrics)
        self.assertEqual(state.history[-1].metrics.input_tokens, 5000)

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


class TestHistoryEntryDispatch(unittest.TestCase):
    """History entries record the dispatch decision emitted by their step (issue #685)."""

    def test_step_dispatch_recorded_on_entry(self):
        d = Dispatch(status="advance", task_hint="dev", complexity="high")
        state = build_state(
            None, workflow="w", step="plan", agent="genius", dispatch=d
        )
        self.assertIs(state.history[-1].dispatch, d)
        self.assertIs(state.dispatch, d)

    def test_entry_dispatch_defaults_to_none(self):
        state = build_state(None, workflow="w", step="plan", agent="genius")
        self.assertIsNone(state.history[-1].dispatch)

    def test_each_entry_carries_its_own_dispatch(self):
        first = build_state(
            None,
            workflow="w",
            step="analyze",
            agent="genius",
            dispatch=Dispatch(status="advance", task_hint="ctx", complexity="high"),
        )
        second = build_state(
            first,
            workflow="w",
            step="context",
            agent="ctx",
            dispatch=Dispatch(status="hold", reason="awaiting input"),
        )
        self.assertEqual(second.history[0].dispatch.status, "advance")
        self.assertEqual(second.history[0].dispatch.task_hint, "ctx")
        self.assertEqual(second.history[1].dispatch.status, "hold")
        # top-level dispatch reflects only the most recent run
        self.assertEqual(second.dispatch.status, "hold")
        self.assertEqual(second.dispatch.reason, "awaiting input")

    def test_history_dispatch_roundtrip(self):
        d = Dispatch(status="loop", task_hint="dev", complexity="low", branch="rejected")
        state = build_state(None, workflow="w", step="plan", agent="genius", dispatch=d)
        restored = WorkflowState.model_validate_json(state.model_dump_json())
        self.assertIsNotNone(restored.history[-1].dispatch)
        self.assertEqual(restored.history[-1].dispatch.status, "loop")
        self.assertEqual(restored.history[-1].dispatch.branch, "rejected")
        self.assertEqual(restored.history[-1].dispatch, restored.dispatch)

    def test_legacy_history_entry_without_dispatch_loads(self):
        data = {
            "version": 3,
            "workflow": "w",
            "step": "plan",
            "agent": "genius",
            "history": [{"step": "analyze", "agent": "genius", "summary": "ok"}],
        }
        state = WorkflowState.model_validate(data)
        self.assertIsNone(state.history[0].dispatch)
