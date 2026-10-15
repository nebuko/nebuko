"""Agent-checkpoint integration in build_output (issue #965).

A folded run (the ``handoff`` tool called with ``fold: true``) saved its
document and asked for a fresh start. It must not advance: ``build_output``
overrides the agent's own turn outcome with INCOMPLETE/AGENT_CHECKPOINT, which
the router treats exactly like a soft kill (re-run the step in place, bounded
by ``MAX_SOFT_RETRIES``).
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    ),
)

from build_output import _agent_checkpoint_outcome, build_output
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message
from workflow_state import Appreciation, TurnOutcome, TurnStatus
from agent_script.features.ensure_pr.ensure_pr import EnsurePrResult
from metrics_schema import Metrics
from agent_script.features.run_agent.run_outcome import OutcomeStatus, RunOutcome


def _make_conversation(events=None, run_outcome=None):
    conv = SimpleNamespace(id=None)
    conv.state = SimpleNamespace(events=events or [])
    conv.run_outcome = run_outcome
    return conv


def _assistant_answer(text):
    return MessageEvent(
        llm_message=Message(role="assistant", content=text),
        source="agent",
    )


class TestAgentCheckpointOutcome(unittest.TestCase):
    def test_outcome_carries_the_checkpoint_reason(self):
        outcome = _agent_checkpoint_outcome()
        self.assertIsInstance(outcome, TurnOutcome)
        self.assertIs(outcome.status, TurnStatus.INCOMPLETE)
        self.assertEqual(outcome.incomplete_reason.value, "agent_checkpoint")

    def test_outcome_is_not_a_completion(self):
        # A checkpoint is incompleteness, so it can never carry a verdict -
        # that is what sends the router back to this step.
        self.assertIsNone(_agent_checkpoint_outcome().appreciation)

    def test_outcome_explains_the_retry_contract(self):
        self.assertIn("MAX_SOFT_RETRIES", _agent_checkpoint_outcome().note)


class TestBuildOutputAgentCheckpoint(unittest.TestCase):
    """End-to-end: a folded run persists the AGENT_CHECKPOINT outcome."""

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

    def test_fold_overrides_an_agent_emitted_verdict(self):
        from agent_script.features.dispatch_next_tool import record_turn_outcome

        answer = "**Work completed**: X"
        conv = _make_conversation(
            events=[_assistant_answer(answer)],
            run_outcome=RunOutcome(status=OutcomeStatus.AGENT_CHECKPOINT),
        )
        conv.id = "conv-checkpoint"
        record_turn_outcome(
            conv.id,
            TurnOutcome(
                status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED
            ),
        )

        state = self._build(conv)["workflow_state"]

        self.assertIs(state.turn_outcome.status, TurnStatus.INCOMPLETE)
        self.assertEqual(
            state.turn_outcome.incomplete_reason.value, "agent_checkpoint"
        )

    def test_successful_run_still_carries_its_verdict(self):
        # Control: without the fold classification the override must not fire.
        from agent_script.features.dispatch_next_tool import record_turn_outcome

        answer = "**Work completed**: X"
        conv = _make_conversation(
            events=[_assistant_answer(answer)],
            run_outcome=RunOutcome(status=OutcomeStatus.SUCCESS),
        )
        conv.id = "conv-checkpoint-control"
        record_turn_outcome(
            conv.id,
            TurnOutcome(
                status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED
            ),
        )

        state = self._build(conv)["workflow_state"]

        self.assertIs(state.turn_outcome.status, TurnStatus.COMPLETED)
        self.assertIs(state.turn_outcome.appreciation, Appreciation.APPROVED)


if __name__ == "__main__":
    unittest.main()
