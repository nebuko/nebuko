"""Per-step soft-kill retry accounting (issue #704).

A soft time-kill must surface an explicit INCOMPLETE turn status and a per-step
retry budget so the workflow re-runs the interrupted step instead of advancing
(issue #964).
"""

import os
import sys
import unittest


from nebuko.engine.lib.workflow_state import (
    IncompleteReason,
    SoftRetryState,
    TurnOutcome,
    TurnStatus,
    WorkflowState,
    build_state,
)


class TestInterruptedOutcomeStatus(unittest.TestCase):
    """The soft-kill/checkpoint reasons are the interruption vocabulary."""

    def test_soft_timeout_is_an_incomplete_reason(self):
        outcome = TurnOutcome(
            status=TurnStatus.INCOMPLETE,
            incomplete_reason=IncompleteReason.SOFT_TIMEOUT,
        )
        self.assertEqual(outcome.status, TurnStatus.INCOMPLETE)
        self.assertEqual(outcome.incomplete_reason, IncompleteReason.SOFT_TIMEOUT)

    def test_agent_checkpoint_is_an_incomplete_reason(self):
        outcome = TurnOutcome(
            status=TurnStatus.INCOMPLETE,
            incomplete_reason=IncompleteReason.AGENT_CHECKPOINT,
        )
        self.assertEqual(outcome.incomplete_reason, IncompleteReason.AGENT_CHECKPOINT)

    def test_incomplete_without_a_reason_is_rejected(self):
        with self.assertRaises(ValueError):
            TurnOutcome(status=TurnStatus.INCOMPLETE)


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
    """`build_state` is the single writer of the counter (issue #704).

    The counter is consumed only for an *engine-issued* retry, signalled by the
    explicit ``soft_retry_attempt`` marker (``SOFT_RETRY_ATTEMPT`` env, emitted
    by ``resolve_soft_kill_route``). A human/manual re-run of a soft-killed step
    carries no marker and must not burn the budget (issue #704 review round 3).
    """

    @staticmethod
    def _soft_killed():
        """The engine-measured outcome of an interrupted run (issue #964)."""
        return TurnOutcome(
            status=TurnStatus.INCOMPLETE,
            incomplete_reason=IncompleteReason.SOFT_TIMEOUT,
        )

    @staticmethod
    def _completed():
        return TurnOutcome(status=TurnStatus.COMPLETED, appreciation="approved")

    def test_engine_retry_increments_the_counter(self):
        prev = build_state(
            None,
            workflow="w",
            step="implement",
            agent="dev",
            turn_outcome=self._soft_killed(),
        )
        nxt = build_state(
            prev,
            workflow="w",
            step="implement",
            agent="dev",
            turn_outcome=self._soft_killed(),
            soft_retry_attempt=True,
        )
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="implement", attempts=1))

    def test_second_engine_retry_increments_again(self):
        prev = build_state(
            None,
            workflow="w",
            step="implement",
            agent="dev",
            turn_outcome=self._soft_killed(),
        ).model_copy(update={"soft_retry": SoftRetryState(step="implement", attempts=1)})
        nxt = build_state(
            prev,
            workflow="w",
            step="implement",
            agent="dev",
            turn_outcome=self._soft_killed(),
            soft_retry_attempt=True,
        )
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="implement", attempts=2))

    def test_human_trigger_after_soft_kill_does_not_consume_the_budget(self):
        # A human/manual re-run of a soft-killed step (no engine marker) must
        # not increment the counter, otherwise the budget silently drains and
        # the step can never be rescued by a human.
        prev = build_state(
            None,
            workflow="w",
            step="implement",
            agent="dev",
            turn_outcome=self._soft_killed(),
        ).model_copy(update={"soft_retry": SoftRetryState(step="implement", attempts=1)})
        nxt = build_state(
            prev,
            workflow="w",
            step="implement",
            agent="dev",
            turn_outcome=self._completed(),
        )
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="implement", attempts=1))

    def test_human_resume_resets_the_budget(self):
        # An explicit human resume takes over: the step restarts with a fresh
        # budget even though the previous vendor run was soft-killed.
        prev = build_state(
            None,
            workflow="w",
            step="implement",
            agent="dev",
            turn_outcome=self._soft_killed(),
        ).model_copy(update={"soft_retry": SoftRetryState(step="implement", attempts=2)})
        nxt = build_state(
            prev,
            workflow="w",
            step="implement",
            agent="dev",
            turn_outcome=self._soft_killed(),
            is_human_resume=True,
        )
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="implement", attempts=0))

    def test_advance_path_resets_the_counter_for_the_new_step(self):
        prev = build_state(
            None, workflow="w", step="implement", agent="dev"
        ).model_copy(
            update={
                "soft_retry": SoftRetryState(step="implement", attempts=2),
                "turn_outcome": self._completed(),
            }
        )
        nxt = build_state(
            prev,
            workflow="w",
            step="review",
            agent="reviewer",
            turn_outcome=self._completed(),
        )
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="review", attempts=0))

    def test_normal_transition_within_a_step_keeps_the_counter(self):
        prev = build_state(
            None, workflow="w", step="implement", agent="dev"
        ).model_copy(
            update={
                "soft_retry": SoftRetryState(step="implement", attempts=1),
                "turn_outcome": self._completed(),
            }
        )
        nxt = build_state(prev, workflow="w", step="implement", agent="dev")
        self.assertEqual(nxt.soft_retry, SoftRetryState(step="implement", attempts=1))


if __name__ == "__main__":
    unittest.main()
