"""Per-step soft-kill retry accounting (issue #704).

A soft time-kill must surface an explicit error status and a per-step retry
budget so the workflow re-runs the interrupted step instead of advancing.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from workflow_state import Dispatch, SoftRetryState, WorkflowState, build_state
from workflow_state.sub_domains.dispatch.dispatch import DISPATCH_STATUSES


class TestDispatchSoftKillStatus(unittest.TestCase):
    def test_soft_kill_is_a_valid_dispatch_status(self):
        self.assertIn("soft_kill", DISPATCH_STATUSES)
        self.assertEqual(Dispatch(status="soft_kill").status, "soft_kill")

    def test_existing_statuses_still_valid(self):
        for status in ("advance", "loop", "hold", "terminate"):
            self.assertEqual(Dispatch(status=status).status, status)

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ValueError):
            Dispatch(status="soft_killed")


class TestSoftRetryStateModel(unittest.TestCase):
    def test_defaults_are_empty_counter(self):
        counter = SoftRetryState()
        self.assertIsNone(counter.step)
        self.assertEqual(counter.attempts, 0)

    def test_workflow_state_default_fills_soft_retry(self):
        self.assertEqual(WorkflowState().soft_retry, SoftRetryState())

    def test_legacy_state_without_key_loads(self):
        legacy = WorkflowState.model_validate(
            {"version": 3, "workflow": "spec-driven-impl", "step": "stub"}
        )
        self.assertIsNone(legacy.soft_retry.step)
        self.assertEqual(legacy.soft_retry.attempts, 0)

    def test_explicit_soft_retry_roundtrips(self):
        state = WorkflowState.model_validate(
            {"soft_retry": {"step": "stub", "attempts": 1}}
        )
        self.assertEqual(state.soft_retry.step, "stub")
        self.assertEqual(state.soft_retry.attempts, 1)


class TestBuildStateCarriesSoftRetry(unittest.TestCase):
    def test_build_state_default_fills_empty_counter(self):
        state = build_state(None, workflow="w", step="implement", agent="dev")
        self.assertEqual(state.soft_retry, SoftRetryState())

    def test_build_state_preserves_previous_counter(self):
        prev = build_state(None, workflow="w", step="implement", agent="dev")
        prev = prev.model_copy(
            update={"soft_retry": SoftRetryState(step="implement", attempts=1)}
        )
        nxt = build_state(prev, workflow="w", step="implement", agent="dev")
        self.assertEqual(nxt.soft_retry.step, "implement")
        self.assertEqual(nxt.soft_retry.attempts, 1)


class TestSoftRetryStateRecord(unittest.TestCase):
    def test_record_starts_a_counter_for_a_new_step(self):
        self.assertEqual(
            SoftRetryState().record("implement"),
            SoftRetryState(step="implement", attempts=1),
        )

    def test_record_advances_an_existing_counter(self):
        counter = SoftRetryState(step="implement", attempts=1).record("implement")
        self.assertEqual(counter, SoftRetryState(step="implement", attempts=2))

    def test_record_restarts_for_a_different_step(self):
        counter = SoftRetryState(step="review", attempts=5).record("implement")
        self.assertEqual(counter, SoftRetryState(step="implement", attempts=1))

    def test_record_does_not_mutate_the_receiver(self):
        base = SoftRetryState(step="implement", attempts=1)
        base.record("implement")
        self.assertEqual(base, SoftRetryState(step="implement", attempts=1))


class TestSoftRetryStateResetFor(unittest.TestCase):
    def test_reset_for_a_new_step_zeroes_the_counter(self):
        counter = SoftRetryState(step="implement", attempts=2).reset_for("review")
        self.assertEqual(counter, SoftRetryState(step="review", attempts=0))

    def test_reset_for_the_same_step_preserves_the_budget(self):
        counter = SoftRetryState(step="implement", attempts=1).reset_for("implement")
        self.assertEqual(counter, SoftRetryState(step="implement", attempts=1))


class TestBuildStateSoftRetryLifecycle(unittest.TestCase):
    """`build_state` is the single writer of the counter (issue #704)."""

    def test_retry_path_increments_the_counter(self):
        prev = build_state(
            None,
            workflow="w",
            step="implement",
            agent="dev",
            dispatch=Dispatch(status="soft_kill"),
        )
        nxt = build_state(
            prev,
            workflow="w",
            step="implement",
            agent="dev",
            dispatch=Dispatch(status="soft_kill"),
        )
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="implement", attempts=1))

    def test_second_retry_increments_again(self):
        prev = build_state(
            None,
            workflow="w",
            step="implement",
            agent="dev",
            dispatch=Dispatch(status="soft_kill"),
        ).model_copy(update={"soft_retry": SoftRetryState(step="implement", attempts=1)})
        nxt = build_state(
            prev,
            workflow="w",
            step="implement",
            agent="dev",
            dispatch=Dispatch(status="soft_kill"),
        )
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="implement", attempts=2))

    def test_advance_path_resets_the_counter_for_the_new_step(self):
        prev = build_state(
            None, workflow="w", step="implement", agent="dev"
        ).model_copy(
            update={
                "soft_retry": SoftRetryState(step="implement", attempts=2),
                "dispatch": Dispatch(status="advance"),
            }
        )
        nxt = build_state(
            prev,
            workflow="w",
            step="review",
            agent="reviewer",
            dispatch=Dispatch(status="advance"),
        )
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="review", attempts=0))

    def test_normal_transition_within_a_step_keeps_the_counter(self):
        prev = build_state(
            None, workflow="w", step="implement", agent="dev"
        ).model_copy(
            update={
                "soft_retry": SoftRetryState(step="implement", attempts=1),
                "dispatch": Dispatch(status="advance"),
            }
        )
        nxt = build_state(prev, workflow="w", step="implement", agent="dev")
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="implement", attempts=1))


if __name__ == "__main__":
    unittest.main()
