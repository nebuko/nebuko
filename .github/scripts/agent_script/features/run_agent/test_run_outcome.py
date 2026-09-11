"""Tests for run outcome classification (graceful interruption, issue #489)."""

import unittest
from types import SimpleNamespace

from agent_script.features.run_agent.run_outcome import (
    OutcomeStatus,
    RunOutcome,
    classify_run,
    status_value,
)


class TestClassifyRun(unittest.TestCase):
    """Test the status + watchdog-flag classification matrix."""

    def test_finished_is_success_even_if_watchdog_fired_race(self):
        # FINISHED wins over the flag (tiny race window after completion).
        self.assertIs(
            classify_run("finished", True), OutcomeStatus.SUCCESS
        )

    def test_paused_with_watchdog_is_graceful_timeout(self):
        self.assertIs(
            classify_run("paused", True), OutcomeStatus.GRACEFULLY_TIMED_OUT
        )

    def test_error_is_failure(self):
        self.assertIs(classify_run("error", False), OutcomeStatus.FAILURE)

    def test_stuck_is_failure(self):
        self.assertIs(classify_run("stuck", False), OutcomeStatus.FAILURE)

    def test_paused_without_watchdog_is_interrupted(self):
        self.assertIs(classify_run("paused", False), OutcomeStatus.INTERRUPTED)

    def test_unexpected_status_defaults_to_success(self):
        self.assertIs(classify_run("idle", False), OutcomeStatus.SUCCESS)
        self.assertIs(classify_run("", False), OutcomeStatus.SUCCESS)

    def test_status_enum_values_are_stable_strings(self):
        # build_output persists these values into workflow state context.
        self.assertEqual(OutcomeStatus.SUCCESS.value, "success")
        self.assertEqual(
            OutcomeStatus.GRACEFULLY_TIMED_OUT.value, "gracefully_timed_out"
        )
        self.assertEqual(OutcomeStatus.FAILURE.value, "failure")
        self.assertEqual(OutcomeStatus.INTERRUPTED.value, "interrupted")


class TestRunOutcome(unittest.TestCase):
    def test_frozen_dataclass_defaults(self):
        outcome = RunOutcome(status=OutcomeStatus.SUCCESS)
        self.assertFalse(outcome.soft_timeout_fired)
        self.assertFalse(outcome.wind_down_completed)

    def test_dispatch_recall_fields_default(self):
        """No recall → count 0 and offset None (legacy no-recall path, #558)."""
        outcome = RunOutcome(status=OutcomeStatus.SUCCESS)
        self.assertEqual(outcome.dispatch_recall_count, 0)
        self.assertIsNone(outcome.dispatch_recall_event_offset)

    def test_recall_fields_set_together_via_replace(self):
        """When a recall fires, run_agent records count AND offset in one
        replace(); both survive on the frozen dataclass (issue #558)."""
        import dataclasses

        outcome = dataclasses.replace(
            RunOutcome(status=OutcomeStatus.SUCCESS),
            dispatch_recall_count=1,
            dispatch_recall_event_offset=42,
        )
        self.assertEqual(outcome.dispatch_recall_count, 1)
        self.assertEqual(outcome.dispatch_recall_event_offset, 42)

    def test_frozen_rejects_mutation(self):
        outcome = RunOutcome(status=OutcomeStatus.SUCCESS)
        with self.assertRaises(Exception):
            outcome.status = OutcomeStatus.FAILURE


class TestStatusValue(unittest.TestCase):
    def test_reads_execution_status_value(self):
        convo = SimpleNamespace(
            execution_status=SimpleNamespace(value="paused")
        )
        self.assertEqual(status_value(convo), "paused")

    def test_missing_attribute_returns_empty_string(self):
        self.assertEqual(status_value(SimpleNamespace()), "")

    def test_none_returns_empty_string(self):
        self.assertEqual(status_value(None), "")


if __name__ == "__main__":
    unittest.main()