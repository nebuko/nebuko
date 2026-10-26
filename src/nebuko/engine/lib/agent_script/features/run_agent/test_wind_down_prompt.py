"""Tests for the wind-down prompt template (graceful interruption, issue #489)."""

import unittest

from nebuko.engine.lib.agent_script.features.run_agent.wind_down_prompt import WIND_DOWN_PROMPT


class TestWindDownPrompt(unittest.TestCase):
    """The prompt must cover every directive the issue asks for."""

    def test_is_non_empty_string(self):
        self.assertIsInstance(WIND_DOWN_PROMPT, str)
        self.assertGreater(len(WIND_DOWN_PROMPT), 100)

    def test_instructs_to_stop(self):
        lowered = WIND_DOWN_PROMPT.lower()
        self.assertIn("stop", lowered)

    def test_asks_for_work_summary(self):
        lowered = WIND_DOWN_PROMPT.lower()
        self.assertIn("work completed", lowered)
        self.assertIn("blocking point", lowered)
        self.assertIn("remaining work", lowered)

    def test_asks_for_remaining_work(self):
        lowered = WIND_DOWN_PROMPT.lower()
        self.assertTrue(
            any(word in lowered for word in ("remain", "block", "next step"))
        )

    def test_asks_to_commit_and_open_pr(self):
        lowered = WIND_DOWN_PROMPT.lower()
        self.assertIn("commit", lowered)
        self.assertIn("push", lowered)
        self.assertIn("open / update the pr", lowered)

    def test_forbids_new_long_tasks(self):
        lowered = WIND_DOWN_PROMPT.lower()
        self.assertTrue(
            "do not start" in lowered or "do not begin" in lowered
        )


if __name__ == "__main__":
    unittest.main()