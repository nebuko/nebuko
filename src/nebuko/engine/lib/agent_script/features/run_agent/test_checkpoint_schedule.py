"""Tests for the checkpoint schedule computation (issue #966).

The schedule is derived from the soft budget so AGENT_SOFT_TIMEOUT_MINUTES
rescales every checkpoint (10/15/20 min at the default 20).
"""

import unittest

from agent_script.features.run_agent.checkpoints import (
    CHECKPOINT_FRACTIONS,
    CheckpointSchedule,
)


class TestCheckpointSchedule(unittest.TestCase):
    """Given a soft budget, when the schedule is derived, delays are its fractions."""

    def test_delays_are_fractions_of_soft_budget(self):
        schedule = CheckpointSchedule.from_soft_timeout(1200.0)
        self.assertEqual(schedule.delays_s, (600.0, 900.0, 1200.0))

    def test_default_fractions_are_50_75_100_percent(self):
        self.assertEqual(CHECKPOINT_FRACTIONS, (0.5, 0.75, 1.0))

    def test_rescales_with_soft_budget(self):
        # AGENT_SOFT_TIMEOUT_MINUTES=10 -> soft_timeout_s=600 -> 300/450/600.
        schedule = CheckpointSchedule.from_soft_timeout(600.0)
        self.assertEqual(schedule.delays_s, (300.0, 450.0, 600.0))

    def test_delay_returns_none_past_the_end(self):
        schedule = CheckpointSchedule.from_soft_timeout(100.0)
        self.assertEqual(schedule.delay(0), 50.0)
        self.assertEqual(schedule.delay(1), 75.0)
        self.assertEqual(schedule.delay(2), 100.0)
        self.assertIsNone(schedule.delay(3))
        self.assertIsNone(schedule.delay(-1))

    def test_len_matches_fraction_count(self):
        schedule = CheckpointSchedule.from_soft_timeout(100.0)
        self.assertEqual(len(schedule), 3)


if __name__ == "__main__":
    unittest.main()
