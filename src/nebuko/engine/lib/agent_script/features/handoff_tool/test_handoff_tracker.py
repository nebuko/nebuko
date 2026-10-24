"""Tests for the handoff tracker (issue #966).

Run-scoped registry of handoff writes and plan-change counts, guarded by a
lock so the tool thread and the run thread can both touch it.
"""

import threading
import time
import unittest

from agent_script.features.handoff_tool.handoff_tracker import HandoffTracker


class TestHandoffTracker(unittest.TestCase):
    def setUp(self):
        # take() pops the run's state; a fresh id per test keeps them isolated.
        self.conversation_id = f"test-{id(self)}"

    def tearDown(self):
        HandoffTracker.take(self.conversation_id)

    def test_take_returns_zeroes_when_absent(self):
        self.assertEqual(HandoffTracker.take(self.conversation_id), (0, 0))

    def test_record_write_accumulates_counts(self):
        HandoffTracker.record_write(self.conversation_id, 2)
        HandoffTracker.record_write(self.conversation_id, 3)
        self.assertEqual(HandoffTracker.take(self.conversation_id), (2, 5))

    def test_take_pops_the_state(self):
        HandoffTracker.record_write(self.conversation_id, 1)
        self.assertEqual(HandoffTracker.take(self.conversation_id), (1, 1))
        self.assertEqual(HandoffTracker.take(self.conversation_id), (0, 0))

    def test_write_count_since_filters_by_epoch(self):
        before = time.time()
        HandoffTracker.record_write(self.conversation_id, 1)
        after = time.time()
        self.assertEqual(
            HandoffTracker.write_count_since(self.conversation_id, before), 1
        )
        self.assertEqual(
            HandoffTracker.write_count_since(self.conversation_id, after), 0
        )

    def test_plan_changes_total_sums_writes(self):
        HandoffTracker.record_write(self.conversation_id, 4)
        HandoffTracker.record_write(self.conversation_id, 1)
        self.assertEqual(HandoffTracker.plan_changes_total(self.conversation_id), 5)

    def test_concurrent_writes_are_not_lost(self):
        def write_many():
            for _ in range(50):
                HandoffTracker.record_write(self.conversation_id, 1)

        threads = [threading.Thread(target=write_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(HandoffTracker.take(self.conversation_id), (200, 200))


if __name__ == "__main__":
    unittest.main()
