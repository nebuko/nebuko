"""Tests for the checkpoint schedule (issue #966).

Covers the acceptance criteria: schedule computation (50/75/100% fractions,
``ValueError`` on a non-positive budget), skip-when-finished, and the
delivery method (queued ``send_message`` — verified against a stub
conversation whose ``arun()`` is in flight while checkpoints fire).
"""

import threading
import time
import unittest

from agent_script.features.run_agent.checkpoints import (
    CHECKPOINT_FRACTIONS,
    CheckpointEvent,
    CheckpointScheduler,
    compute_checkpoints,
    count_plan_changes,
)


class TestComputeCheckpoints(unittest.TestCase):
    def test_fractions_of_default_budget(self):
        events = compute_checkpoints(20 * 60)
        self.assertEqual([e.offset_s for e in events], [600.0, 900.0, 1200.0])

    def test_labels_follow_fractions(self):
        events = compute_checkpoints(100.0)
        self.assertEqual(
            [e.label for e in events],
            [label for label, _ in CHECKPOINT_FRACTIONS],
        )
        for event in events:
            self.assertIsInstance(event, CheckpointEvent)
            self.assertTrue(event.prompt.strip())

    def test_scales_with_budget(self):
        for events in (compute_checkpoints(40.0), compute_checkpoints(40.0)):
            self.assertEqual(
                [e.offset_s for e in events], [20.0, 30.0, 40.0]
            )

    def test_non_positive_budget_raises(self):
        for bad in (0, -1, -0.5):
            with self.assertRaises(ValueError):
                compute_checkpoints(bad)


class TestCheckpointScheduler(unittest.TestCase):
    def _events(self) -> tuple:
        return compute_checkpoints(0.3)  # 0.15s / 0.225s / 0.3s

    def test_fires_in_order_while_running(self):
        delivered = []
        scheduler = CheckpointScheduler(
            self._events(), delivered.append, lambda: False
        ).arm()
        time.sleep(0.45)
        scheduler.cancel()
        self.assertEqual(delivered[0].label, "checkpoint_50")
        self.assertEqual(len(delivered), 3)

    def test_skips_when_finished(self):
        delivered = []
        scheduler = CheckpointScheduler(
            self._events(), delivered.append, lambda: True
        ).arm()
        time.sleep(0.45)
        scheduler.cancel()
        self.assertEqual(delivered, [])

    def test_cancel_skips_pending(self):
        delivered = []
        scheduler = CheckpointScheduler(
            self._events(), delivered.append, lambda: False
        ).arm()
        scheduler.cancel()
        time.sleep(0.4)
        self.assertEqual(delivered, [])

    def test_delivery_is_queued_not_interrupting(self):
        """Delivery runs on the timer thread while arun() is in flight.

        The queued send_message path must not cancel the running arun():
        the stub run keeps a flag set that only an interrupt would clear.
        """
        delivered = []
        interrupted = threading.Event()

        class StubConversation:
            def send_message(self, message):
                delivered.append(message)

            def interrupt(self):
                interrupted.set()

        conversation = StubConversation()
        scheduler = CheckpointScheduler(
            self._events(),
            lambda event: conversation.send_message(event.prompt),
            lambda: False,
        ).arm()
        time.sleep(0.45)
        scheduler.cancel()
        self.assertFalse(interrupted.is_set())
        self.assertEqual(len(delivered), 3)
        self.assertIn("checkpoint", delivered[0].lower())


class TestCountPlanChanges(unittest.TestCase):
    def test_counts_bullets_in_section(self):
        text = (
            "# Session handoff\n"
            "## State\n\nbusy\n"
            "## Plan changes\n\n"
            "- dropped the cache layer\n"
            "- narrowed scope to parser\n"
            "\n## Next steps\n\n1. run tests\n"
        )
        self.assertEqual(count_plan_changes(text), 2)

    def test_empty_section_counts_zero(self):
        self.assertEqual(count_plan_changes("## Plan changes\n\n## Next steps"), 0)

    def test_no_section_counts_zero(self):
        self.assertEqual(count_plan_changes("# Session handoff\nnothing"), 0)


if __name__ == "__main__":
    unittest.main()
