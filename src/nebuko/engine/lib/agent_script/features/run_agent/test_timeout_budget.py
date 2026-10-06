"""Tests for timeout budget resolution (graceful interruption, issue #489)."""

import os
import unittest

from agent_script.features.run_agent.timeout_budget import (
    DEFAULT_SOFT_TIMEOUT_MINUTES,
    DEFAULT_WIND_DOWN_TIMEOUT_MINUTES,
    TimeoutBudget,
    resolve_timeout_budget,
)


class TestResolveTimeoutBudget(unittest.TestCase):
    """Test env-based budget resolution and fallbacks."""

    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("AGENT_SOFT_TIMEOUT_MINUTES", "WIND_DOWN_TIMEOUT_MINUTES")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_defaults_when_unset(self):
        os.environ.pop("AGENT_SOFT_TIMEOUT_MINUTES", None)
        os.environ.pop("WIND_DOWN_TIMEOUT_MINUTES", None)
        budget = resolve_timeout_budget()
        self.assertEqual(budget.soft_timeout_s, DEFAULT_SOFT_TIMEOUT_MINUTES * 60)
        self.assertEqual(
            budget.wind_down_timeout_s, DEFAULT_WIND_DOWN_TIMEOUT_MINUTES * 60
        )

    def test_env_override(self):
        os.environ["AGENT_SOFT_TIMEOUT_MINUTES"] = "100"
        os.environ["WIND_DOWN_TIMEOUT_MINUTES"] = "5"
        budget = resolve_timeout_budget()
        self.assertEqual(budget.soft_timeout_s, 6000)
        self.assertEqual(budget.wind_down_timeout_s, 300)

    def test_invalid_values_fall_back_to_defaults(self):
        os.environ["AGENT_SOFT_TIMEOUT_MINUTES"] = "not-a-number"
        os.environ["WIND_DOWN_TIMEOUT_MINUTES"] = "abc"
        budget = resolve_timeout_budget()
        self.assertEqual(budget.soft_timeout_s, DEFAULT_SOFT_TIMEOUT_MINUTES * 60)
        self.assertEqual(
            budget.wind_down_timeout_s, DEFAULT_WIND_DOWN_TIMEOUT_MINUTES * 60
        )

    def test_non_positive_values_fall_back_to_defaults(self):
        os.environ["AGENT_SOFT_TIMEOUT_MINUTES"] = "0"
        os.environ["WIND_DOWN_TIMEOUT_MINUTES"] = "-3"
        budget = resolve_timeout_budget()
        self.assertEqual(budget.soft_timeout_s, DEFAULT_SOFT_TIMEOUT_MINUTES * 60)
        self.assertEqual(
            budget.wind_down_timeout_s, DEFAULT_WIND_DOWN_TIMEOUT_MINUTES * 60
        )

    def test_fractional_minutes_supported(self):
        os.environ["AGENT_SOFT_TIMEOUT_MINUTES"] = "0.5"
        budget = resolve_timeout_budget()
        self.assertEqual(budget.soft_timeout_s, 30)

    def test_budget_is_frozen_dataclass(self):
        budget = resolve_timeout_budget()
        with self.assertRaises(Exception):
            budget.soft_timeout_s = 1  # frozen dataclass rejects mutation

    def test_soft_budget_stays_under_gha_limit(self):
        # GHA hard timeout is 120 min; soft + wind-down must leave margin.
        budget = resolve_timeout_budget()
        self.assertLess(budget.soft_timeout_s + budget.wind_down_timeout_s, 120 * 60)


class TestTimeoutBudgetDataclass(unittest.TestCase):
    def test_direct_construction(self):
        budget = TimeoutBudget(soft_timeout_s=1234.0, wind_down_timeout_s=56.0)
        self.assertEqual(budget.soft_timeout_s, 1234.0)
        self.assertEqual(budget.wind_down_timeout_s, 56.0)


if __name__ == "__main__":
    unittest.main()