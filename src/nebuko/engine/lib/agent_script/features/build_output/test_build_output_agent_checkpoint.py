"""Agent-checkpoint integration in build_output (issue #965).

A folded run (the ``handoff`` tool called with ``fold: true``) saved its
document and asked for a fresh start. It must not advance: ``build_output``
overrides the agent's own dispatch with the explicit ``agent_checkpoint``
status, which the router treats exactly like ``soft_kill`` (re-run the step in
place, bounded by ``MAX_SOFT_RETRIES``).
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

from build_output import _agent_checkpoint_dispatch, build_output
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message
from workflow_state import Dispatch
from agent_script.features.ensure_pr.ensure_pr import EnsurePrResult
from metrics_schema import Metrics
from agent_script.features.run_agent.run_outcome import OutcomeStatus, RunOutcome


def _make_conversation(events=None, run_outcome=None):
    conv = SimpleNamespace()
    conv.state = SimpleNamespace(events=events or [])
    conv.run_outcome = run_outcome
    return conv


def _assistant_answer(text):
    return MessageEvent(
        llm_message=Message(role="assistant", content=text),
        source="agent",
    )


class TestAgentCheckpointDispatch(unittest.TestCase):
    def test_dispatch_carries_the_checkpoint_status(self):
        dispatch = _agent_checkpoint_dispatch()
        self.assertIsInstance(dispatch, Dispatch)
        self.assertEqual(dispatch.status, "agent_checkpoint")

    def test_dispatch_is_not_a_normal_advance(self):
        self.assertNotEqual(_agent_checkpoint_dispatch().status, "advance")

    def test_dispatch_explains_the_retry_contract(self):
        reason = _agent_checkpoint_dispatch().reason
        self.assertIn("MAX_SOFT_RETRIES", reason)


class TestBuildOutputAgentCheckpoint(unittest.TestCase):
    """End-to-end: a folded run persists the ``agent_checkpoint`` dispatch."""

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

    def test_fold_overrides_an_agent_emitted_advance_dispatch(self):
        answer = (
            "**Work completed**: X\n"
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        )
        conv = _make_conversation(
            events=[_assistant_answer(answer)],
            run_outcome=RunOutcome(status=OutcomeStatus.AGENT_CHECKPOINT),
        )

        state = self._build(conv)["workflow_state"]

        self.assertEqual(state.dispatch.status, "agent_checkpoint")

    def test_successful_run_still_advances(self):
        # Control: without the fold classification the override must not fire.
        answer = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        conv = _make_conversation(
            events=[_assistant_answer(answer)],
            run_outcome=RunOutcome(status=OutcomeStatus.SUCCESS),
        )

        state = self._build(conv)["workflow_state"]

        self.assertEqual(state.dispatch.status, "advance")


if __name__ == "__main__":
    unittest.main()
