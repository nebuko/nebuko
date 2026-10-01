"""Soft-kill retry-vs-halt routing (issue #704).

Given a ``soft_kill`` dispatch, the router re-runs the interrupted step while
its per-step retry budget remains and halts the workflow for a human once the
budget is exhausted — never advancing to the next step.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resolve_config.features.resolve_route.resolve_route import (
    _resolve_dispatch_path,
    resolve_max_soft_retries,
    resolve_soft_kill_route,
)
from workflow_state import Dispatch, SoftRetryState, WorkflowState


def _state(step="implement", agent="dev", attempts=0, counter_step="implement"):
    return WorkflowState(
        workflow="dev-review-loop",
        step=step,
        agent=agent,
        soft_retry=SoftRetryState(step=counter_step, attempts=attempts),
    )


class TestSoftKillRoute(unittest.TestCase):
    def test_non_soft_kill_dispatch_falls_through(self):
        result = resolve_soft_kill_route(
            _state(), Dispatch(status="advance"), max_soft_retries=2
        )
        self.assertIsNone(result)

    def test_retries_the_same_step_while_budget_remains(self):
        result = resolve_soft_kill_route(
            _state(attempts=0), Dispatch(status="soft_kill"), max_soft_retries=2
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")
        self.assertEqual(result["config_step"], "implement")
        self.assertEqual(result["task"], "dev")

    def test_second_retry_is_still_within_budget(self):
        result = resolve_soft_kill_route(
            _state(attempts=1), Dispatch(status="soft_kill"), max_soft_retries=2
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")
        self.assertEqual(result["config_step"], "implement")

    def test_exhausted_budget_halts_for_a_human(self):
        result = resolve_soft_kill_route(
            _state(attempts=2), Dispatch(status="soft_kill"), max_soft_retries=2
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("implement", result["REASON"])

    def test_zero_budget_halts_immediately(self):
        result = resolve_soft_kill_route(
            _state(attempts=0), Dispatch(status="soft_kill"), max_soft_retries=0
        )
        self.assertEqual(result["SKIP_AGENT"], "true")

    def test_counter_of_a_different_step_does_not_consume_budget(self):
        result = resolve_soft_kill_route(
            _state(step="review", agent="reviewer", attempts=5, counter_step="implement"),
            Dispatch(status="soft_kill"),
            max_soft_retries=2,
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")
        self.assertEqual(result["config_step"], "review")


class TestResolveMaxSoftRetries(unittest.TestCase):
    """The single, validated read site for the retry budget (issue #704)."""

    def test_reads_the_declared_value(self):
        self.assertEqual(resolve_max_soft_retries({"MAX_SOFT_RETRIES": "3"}), 3)

    def test_defaults_to_two_when_unset(self):
        self.assertEqual(resolve_max_soft_retries({}), 2)

    def test_defaults_when_malformed(self):
        self.assertEqual(resolve_max_soft_retries({"MAX_SOFT_RETRIES": "many"}), 2)

    def test_negative_is_clamped_to_zero(self):
        self.assertEqual(resolve_max_soft_retries({"MAX_SOFT_RETRIES": "-1"}), 0)


class TestDispatchPathSoftKillWiring(unittest.TestCase):
    """`_resolve_dispatch_path` must consult the soft-kill router first."""

    def test_soft_kill_dispatch_retries_in_place(self):
        result = _resolve_dispatch_path(
            _state(attempts=0),
            Dispatch(status="soft_kill"),
            "workflows",
        )
        self.assertEqual(result["config_step"], "implement")
        self.assertEqual(result["task"], "dev")


if __name__ == "__main__":
    unittest.main()
