"""Tests for the failure-summary integration in build_output (issue #668)."""

import unittest

from build_output import _build_failure_summary
from agent_script.features.run_agent.run_failure import (
    RESUME_COMMAND,
    RunFailure,
    RunFailureKind,
)
from agent_script.features.run_agent.run_outcome import OutcomeStatus, RunOutcome


def _failure_outcome(kind: RunFailureKind, summary: str) -> RunOutcome:
    return RunOutcome(
        status=OutcomeStatus.FAILURE,
        failure=RunFailure(kind=kind, summary=summary, detail="raw error text"),
    )


class TestBuildFailureSummary(unittest.TestCase):
    def test_unreachable_summary_names_cause_and_resume_command(self):
        outcome = _failure_outcome(
            RunFailureKind.LLM_UNREACHABLE,
            "The agent could not reach the LLM API provider.",
        )
        text = _build_failure_summary(outcome)
        self.assertIn("LLM API provider", text)
        self.assertIn(RESUME_COMMAND, text)

    def test_generic_failure_still_prints_resume_command(self):
        outcome = _failure_outcome(RunFailureKind.UNKNOWN, "The run failed.")
        text = _build_failure_summary(outcome)
        self.assertIn(RESUME_COMMAND, text)
        self.assertNotIn("unreachable", text.lower())

    def test_detail_is_rendered_collapsed(self):
        outcome = _failure_outcome(RunFailureKind.UNKNOWN, "The run failed.")
        text = _build_failure_summary(outcome)
        self.assertIn("<details>", text)
        self.assertIn("raw error text", text)


if __name__ == "__main__":
    unittest.main()
