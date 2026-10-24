"""Tests for the RunOutcome checkpoint fields (issue #966)."""

import unittest

from agent_script.features.run_agent.run_outcome import OutcomeStatus, RunOutcome


class TestRunOutcomeCheckpointFields(unittest.TestCase):
    """Given a run outcome, when checkpoints fired, the fields carry the counts."""

    def test_default_checkpoint_count_is_zero(self):
        outcome = RunOutcome(status=OutcomeStatus.SUCCESS)
        self.assertEqual(outcome.checkpoint_count, 0)
        self.assertIsNone(outcome.plan_changes_total)

    def test_checkpoint_fields_are_settable(self):
        outcome = RunOutcome(
            status=OutcomeStatus.GRACEFULLY_TIMED_OUT,
            soft_timeout_fired=True,
            checkpoint_count=3,
            plan_changes_total=5,
        )
        self.assertEqual(outcome.checkpoint_count, 3)
        self.assertEqual(outcome.plan_changes_total, 5)


if __name__ == "__main__":
    unittest.main()
