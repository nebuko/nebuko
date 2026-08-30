"""Tests for the run watchdog (graceful interruption, issue #489)."""

import time
import unittest

from agent_script.features.run_agent.run_watchdog import RunWatchdog


class TestRunWatchdog(unittest.TestCase):
    """Test armed one-shot timer behaviour."""

    def test_fires_callback_after_delay(self):
        fired = []
        watchdog = RunWatchdog(0.05, lambda: fired.append(1)).arm()
        time.sleep(0.15)
        self.assertTrue(watchdog.fired)
        self.assertEqual(fired, [1])

    def test_callback_invoked_exactly_once(self):
        fired = []
        watchdog = RunWatchdog(0.05, lambda: fired.append(1)).arm()
        time.sleep(0.15)
        time.sleep(0.15)
        self.assertEqual(fired, [1])

    def test_cancel_prevents_firing(self):
        watchdog = RunWatchdog(5.0, lambda: self.fail("should not fire")).arm()
        watchdog.cancel()
        time.sleep(0.1)
        self.assertFalse(watchdog.fired)

    def test_arm_is_idempotent(self):
        watchdog = RunWatchdog(0.05, lambda: None)
        watchdog.arm().arm()
        time.sleep(0.15)
        self.assertTrue(watchdog.fired)

    def test_cancel_after_fire_is_noop(self):
        watchdog = RunWatchdog(0.05, lambda: None).arm()
        time.sleep(0.15)
        watchdog.cancel()  # must not raise
        self.assertTrue(watchdog.fired)

    def test_delay_property(self):
        self.assertEqual(RunWatchdog(42.5, lambda: None).delay_s, 42.5)

    def test_callback_exception_does_not_escape(self):
        def boom():
            raise RuntimeError("boom")

        watchdog = RunWatchdog(0.05, boom).arm()
        time.sleep(0.15)
        # Flag is set even though the callback raised; error was logged.
        self.assertTrue(watchdog.fired)


if __name__ == "__main__":
    unittest.main()