"""Tests for the graceful-timeout integration in build_output (issue #489)."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from build_output import (
    _build_timeout_summary,
    _get_run_outcome,
    build_output,
)
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message
from agent_script.features.ensure_pr.ensure_pr import EnsurePrResult
from metrics_schema import Metrics
from agent_script.features.run_agent.run_outcome import (
    OutcomeStatus,
    RunOutcome,
)


def _make_conversation(events=None, run_outcome=None):
    """Minimal stand-in for an OpenHands Conversation."""
    conv = SimpleNamespace()
    conv.state = SimpleNamespace(events=events or [])
    conv.run_outcome = run_outcome
    return conv


class TestRunOutcomeAccessors(unittest.TestCase):
    def test_get_run_outcome_present(self):
        outcome = RunOutcome(status=OutcomeStatus.SUCCESS)
        conv = _make_conversation(run_outcome=outcome)
        self.assertIs(_get_run_outcome(conv), outcome)

    def test_get_run_outcome_absent(self):
        conv = SimpleNamespace(state=SimpleNamespace(events=[]))
        self.assertIsNone(_get_run_outcome(conv))

    def test_build_timeout_summary_mentions_checkpoint(self):
        pr = EnsurePrResult(pr_url="https://github.com/acme/repo/pull/9")
        text = _build_timeout_summary(pr)
        self.assertIn("gracefully interrupted", text)
        self.assertIn("recovery log", text)
        self.assertIn("https://github.com/acme/repo/pull/9", text)

    def test_build_timeout_summary_without_pr(self):
        text = _build_timeout_summary(None)
        self.assertIn("gracefully interrupted", text)


class TestBuildOutputGracefulTimeout(unittest.TestCase):
    """End-to-end build_output behaviour when the run was gracefully timed out."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        env_patch = patch.dict(
            "os.environ",
            {
                "ROLE_NAME": "dev",
                "WORKFLOW_NAME": "default",
                "WORKFLOW_STEP": "implement",
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _run(self, run_outcome, pr_result=None):
        conv = _make_conversation(run_outcome=run_outcome)
        ensure_pr_mock = (
            lambda: pr_result
            if pr_result is not None
            else EnsurePrResult(skipped=True, message="no git remote")
        )
        with patch("build_output.ensure_pr", side_effect=ensure_pr_mock) as ep:
            result = build_output(
                conv, Metrics(agent_role="dev"), None, prev_state=None
            )
        return result, ep

    def test_graceful_timeout_runs_ensure_pr_and_records_status(self):
        pr = EnsurePrResult(
            pushed=True,
            pr_url="https://github.com/acme/repo/pull/9",
            message="PR opened",
        )
        result, ep = self._run(
            RunOutcome(
                status=OutcomeStatus.GRACEFULLY_TIMED_OUT,
                soft_timeout_fired=True,
            ),
            pr_result=pr,
        )
        ep.assert_called_once()
        state = result["workflow_state"]
        self.assertEqual(
            state.current_context.get("last_run_status"), "gracefully_timed_out"
        )
        # The wind-down produced no final message → synthesized summary.
        self.assertIn("gracefully interrupted", result["final_message"])
        self.assertIn("https://github.com/acme/repo/pull/9", result["final_message"])

    def test_graceful_timeout_with_wind_down_summary_keeps_it(self):
        # A wind-down that DID finish produces a real final message.
        text = (
            "**Work completed**: X\n"
            "**Blocking point**: Y\n"
            "**Remaining work**: Z"
        )
        msg = MessageEvent(
            llm_message=Message(role="assistant", content=text),
            source="agent",
        )
        conv = _make_conversation(
            events=[msg],
            run_outcome=RunOutcome(
                status=OutcomeStatus.GRACEFULLY_TIMED_OUT,
                soft_timeout_fired=True,
                wind_down_completed=True,
            ),
        )
        ensure_pr_mock = (
            lambda: EnsurePrResult(skipped=True, message="no git remote")
        )
        with patch("build_output.ensure_pr", side_effect=ensure_pr_mock) as ep:
            result = build_output(
                conv, Metrics(agent_role="dev"), None, prev_state=None
            )
        ep.assert_called_once()
        self.assertIn("**Work completed**", result["final_message"])
        self.assertEqual(
            result["workflow_state"].current_context.get("last_run_status"),
            "gracefully_timed_out",
        )

    def test_success_records_last_run_status_without_ensure_pr(self):
        conv = _make_conversation(
            run_outcome=RunOutcome(status=OutcomeStatus.SUCCESS)
        )
        with patch("build_output.ensure_pr") as ep:
            result = build_output(
                conv, Metrics(agent_role="dev"), None, prev_state=None
            )
        ep.assert_not_called()
        self.assertEqual(
            result["workflow_state"].current_context.get("last_run_status"),
            "success",
        )

    def test_no_run_outcome_leaves_status_unset(self):
        conv = _make_conversation()
        with patch("build_output.ensure_pr") as ep:
            result = build_output(
                conv, Metrics(agent_role="dev"), None, prev_state=None
            )
        ep.assert_not_called()
        self.assertNotIn(
            "last_run_status", result["workflow_state"].current_context
        )


if __name__ == "__main__":
    unittest.main()