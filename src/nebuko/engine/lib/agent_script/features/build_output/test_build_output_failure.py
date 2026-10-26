"""Tests for the failure-summary integration in build_output (issue #668)."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from build_output import _build_failure_summary, build_output
from nebuko.engine.lib.metrics_schema import Metrics
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message
from nebuko.engine.lib.agent_script.features.run_agent.run_failure import (
    RESUME_COMMAND,
    RunFailure,
    RunFailureKind,
)
from nebuko.engine.lib.agent_script.features.run_agent.run_outcome import OutcomeStatus, RunOutcome


def _failure_outcome(kind: RunFailureKind, summary: str) -> RunOutcome:
    return RunOutcome(
        status=OutcomeStatus.FAILURE,
        failure=RunFailure(kind=kind, summary=summary, detail="raw error text"),
    )


def _make_conversation(events=None, run_outcome=None):
    """Minimal stand-in for an OpenHands Conversation."""
    conv = SimpleNamespace()
    conv.state = SimpleNamespace(events=events or [])
    conv.run_outcome = run_outcome
    return conv


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


class TestBuildOutputFailure(unittest.TestCase):
    """End-to-end build_output behaviour when the run crashed (no output)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        env_patch = patch.dict(
            "os.environ",
            {
                "ROLE_NAME": "dev",
                "WORKFLOW_NAME": "spec-driven-impl",
                "WORKFLOW_STEP": "implement",
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_crashed_run_synthesizes_failure_summary(self):
        outcome = _failure_outcome(
            RunFailureKind.LLM_UNREACHABLE,
            "The agent could not reach the LLM API provider.",
        )
        conv = _make_conversation(run_outcome=outcome)
        with patch("build_output.ensure_pr") as ep:
            result = build_output(
                conv, Metrics(agent_role="dev"), None, prev_state=None
            )
        ep.assert_not_called()
        # The terminal comment names the cause and the recovery command.
        self.assertIn("LLM API provider", result["final_message"])
        self.assertIn(RESUME_COMMAND, result["final_message"])
        self.assertEqual(
            result["workflow_state"].current_context.get("last_run_status"),
            "failure",
        )

    def test_status_failure_without_error_still_gets_generic_summary(self):
        # A FAILURE classified from the conversation status (no escaped
        # exception) must still get an honest generic message + resume hint.
        outcome = RunOutcome(status=OutcomeStatus.FAILURE)
        conv = _make_conversation(run_outcome=outcome)
        with patch("build_output.ensure_pr") as ep:
            result = build_output(
                conv, Metrics(agent_role="dev"), None, prev_state=None
            )
        ep.assert_not_called()
        self.assertIn(RESUME_COMMAND, result["final_message"])
        self.assertNotIn("unreachable", result["final_message"].lower())

    def test_a_crash_overrides_partial_prose_with_the_notification(self):
        """A crashed run is a notification, whatever prose it left behind.

        Partial prose is not a deliverable (issue #978): the notification
        summary replaces it, so the comment cannot carry the routing payload
        of a turn that never completed.
        """
        text = "**Work completed**: X"
        msg = MessageEvent(
            llm_message=Message(role="assistant", content=text),
            source="agent",
        )
        outcome = _failure_outcome(RunFailureKind.UNKNOWN, "The run failed.")
        conv = _make_conversation(events=[msg], run_outcome=outcome)
        with patch("build_output.ensure_pr") as ep:
            result = build_output(
                conv, Metrics(agent_role="dev"), None, prev_state=None
            )
        ep.assert_not_called()
        self.assertIn("The run failed.", result["final_message"])
        self.assertIn(RESUME_COMMAND, result["final_message"])
        state = result["workflow_state"]
        self.assertEqual(state.turn_outcome.status.value, "incomplete")
        self.assertEqual(state.turn_outcome.incomplete_reason.value, "failure")
        self.assertIsNone(state.turn_outcome.appreciation)


if __name__ == "__main__":
    unittest.main()
