"""Tests for the sequential checkpoint scheduler (issue #966).

Timers fire at absolute delays measured from ``arm()`` (not from the previous
fire); a callback exception must not halt the chain; ``cancel()`` stops it.
"""

import time
import unittest

from agent_script.features.run_agent.checkpoints import (
    CheckpointSchedule,
    CheckpointScheduler,
)


def _schedule(*delays: float) -> CheckpointSchedule:
    return CheckpointSchedule(delays_s=tuple(delays))


class TestCheckpointScheduler(unittest.TestCase):
    """Given an armed scheduler, when delays elapse, checkpoints fire in order."""

    def test_fires_sequentially_at_absolute_delays(self):
        fired = []
        CheckpointScheduler(
            _schedule(0.05, 0.1, 0.15), lambda i: fired.append(i)
        ).arm()
        time.sleep(0.4)
        self.assertEqual(fired, [0, 1, 2])

    def test_delays_are_measured_from_arm_not_previous_fire(self):
        # A slow callback must not shift the next checkpoint's deadline.
        def on_checkpoint(index):
            if index == 0:
                time.sleep(0.08)

        fired = []
        CheckpointScheduler(_schedule(0.05, 0.1), lambda i: fired.append(i)).arm()
        time.sleep(0.3)
        self.assertEqual(fired, [0, 1])

    def test_cancel_stops_the_chain(self):
        fired = []
        scheduler = CheckpointScheduler(
            _schedule(0.05, 0.1), lambda i: fired.append(i)
        ).arm()
        scheduler.cancel()
        time.sleep(0.2)
        self.assertEqual(fired, [0])  # first timer already fired or not — chain stopped

    def test_cancel_is_idempotent(self):
        scheduler = CheckpointScheduler(_schedule(5.0), lambda i: None).arm()
        scheduler.cancel()
        scheduler.cancel()  # must not raise

    def test_callback_exception_does_not_halt_the_chain(self):
        fired = []

        def on_checkpoint(index):
            fired.append(index)
            if index == 0:
                raise RuntimeError("boom")

        CheckpointScheduler(_schedule(0.05, 0.1), on_checkpoint).arm()
        time.sleep(0.3)
        self.assertEqual(fired, [0, 1])

    def test_fired_count_tracks_fired_checkpoints(self):
        scheduler = CheckpointScheduler(_schedule(0.05, 0.1), lambda i: None).arm()
        time.sleep(0.3)
        self.assertEqual(scheduler.fired_count, 2)

    def test_unarmed_scheduler_fires_nothing(self):
        fired = []
        CheckpointScheduler(_schedule(0.05), lambda i: fired.append(i))
        time.sleep(0.15)
        self.assertEqual(fired, [])


if __name__ == "__main__":
    unittest.main()
