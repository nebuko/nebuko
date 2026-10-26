"""Soft-kill integration in build_output (issue #704).

A gracefully timed-out run must record an INCOMPLETE/SOFT_TIMEOUT turn
outcome so the workflow re-runs the interrupted step instead of advancing.
The ``TestBuildOutputSoftKill`` suite drives ``build_output`` end to end -
rather than only the ``_soft_kill_outcome`` helper - to prove the override
reaches the persisted workflow state even when the agent emitted its own
verdict.
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nebuko.engine.lib.agent_script.features.build_output.build_output import (
    _soft_kill_outcome,
    build_output,
)
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message
from nebuko.engine.lib.workflow_state import Appreciation, TurnOutcome, TurnStatus
from nebuko.engine.lib.agent_script.features.ensure_pr.ensure_pr import EnsurePrResult
from nebuko.engine.lib.metrics_schema import Metrics
from nebuko.engine.lib.agent_script.features.run_agent.run_outcome import OutcomeStatus, RunOutcome


def _make_conversation(events=None, run_outcome=None):
    """Minimal stand-in for an OpenHands Conversation."""
    conv = SimpleNamespace(id=None)
    conv.state = SimpleNamespace(events=events or [])
    conv.run_outcome = run_outcome
    return conv


def _assistant_answer(text):
    return MessageEvent(
        llm_message=Message(role="assistant", content=text),
        source="agent",
    )


def _timed_out(events=None, **outcome_kwargs):
    return _make_conversation(
        events=events,
        run_outcome=RunOutcome(
            status=OutcomeStatus.GRACEFULLY_TIMED_OUT,
            soft_timeout_fired=True,
            **outcome_kwargs,
        ),
    )


class TestSoftKillOutcome(unittest.TestCase):
    def test_outcome_carries_the_soft_timeout_reason(self):
        outcome = _soft_kill_outcome()
        self.assertIsInstance(outcome, TurnOutcome)
        self.assertIs(outcome.status, TurnStatus.INCOMPLETE)
        self.assertEqual(outcome.incomplete_reason.value, "soft_timeout")

    def test_outcome_is_not_a_completion(self):
        # A soft kill is engine-measured incompleteness, so it can never carry
        # an agent verdict - that is what sends the router back to this step.
        outcome = _soft_kill_outcome()
        self.assertIsNone(outcome.appreciation)

    def test_outcome_explains_the_retry_contract(self):
        self.assertIn("MAX_SOFT_RETRIES", _soft_kill_outcome().note)


class TestBuildOutputSoftKill(unittest.TestCase):
    """End-to-end: a soft-killed run persists the INCOMPLETE outcome."""

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
                # Keep the disk hand-off hermetic: point RUNNER_TEMP at the
                # sandbox so the codec cannot read a live runner's state file.
                "RUNNER_TEMP": self._tmp.name,
                "WORKFLOWS_DIR": self._tmp.name,
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _build(self, conv):
        with patch(
            "build_output.ensure_pr",
            return_value=EnsurePrResult(skipped=True, message="no git remote"),
        ):
            return build_output(conv, Metrics(agent_role="dev"), None, prev_state=None)

    def test_soft_kill_is_emitted_as_the_turn_outcome(self):
        state = self._build(_timed_out())["workflow_state"]
        self.assertIsNotNone(state.turn_outcome)
        self.assertIs(state.turn_outcome.status, TurnStatus.INCOMPLETE)
        self.assertEqual(state.turn_outcome.incomplete_reason.value, "soft_timeout")

    def test_soft_kill_overrides_an_agent_emitted_verdict(self):
        # The wind-down finished with its own recorded verdict - the
        # grace-timeout override must still win.
        answer = "**Work completed**: X"
        state = self._build(
            _timed_out(events=[_assistant_answer(answer)], wind_down_completed=True)
        )["workflow_state"]
        self.assertIs(state.turn_outcome.status, TurnStatus.INCOMPLETE)

    def test_agent_verdict_survives_when_run_did_not_time_out(self):
        # Control: the same recorded verdict really persists, so the test
        # above exercises the override rather than a dropped outcome.
        from nebuko.engine.lib.agent_script.features.dispatch_next_tool import record_turn_outcome

        answer = "**Work completed**: X"
        conv = _make_conversation(
            events=[_assistant_answer(answer)],
            run_outcome=RunOutcome(status=OutcomeStatus.SUCCESS),
        )
        conv.id = "conv-soft-kill-control"
        record_turn_outcome(
            conv.id,
            TurnOutcome(
                status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED
            ),
        )
        state = self._build(conv)["workflow_state"]
        self.assertIs(state.turn_outcome.status, TurnStatus.COMPLETED)
        self.assertIs(state.turn_outcome.appreciation, Appreciation.APPROVED)
