"""Soft-kill integration in build_output (issue #704).

A gracefully timed-out run must emit an explicit soft-kill error dispatch so
the workflow retries the interrupted step instead of advancing. The
``TestBuildOutputSoftKill`` suite drives ``build_output`` end to end — rather
than only the ``_soft_kill_dispatch`` helper — to prove the override reaches
the persisted workflow state even when the agent emitted its own dispatch.
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

from build_output import _soft_kill_dispatch, build_output
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message
from workflow_state import Dispatch
from agent_script.features.ensure_pr.ensure_pr import EnsurePrResult
from metrics_schema import Metrics
from agent_script.features.run_agent.run_outcome import OutcomeStatus, RunOutcome


def _make_conversation(events=None, run_outcome=None):
    """Minimal stand-in for an OpenHands Conversation."""
    conv = SimpleNamespace()
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



class TestSoftKillDispatch(unittest.TestCase):
    def test_dispatch_carries_the_soft_kill_error_status(self):
        dispatch = _soft_kill_dispatch()
        self.assertIsInstance(dispatch, Dispatch)
        self.assertEqual(dispatch.status, "soft_kill")

    def test_dispatch_is_not_a_normal_advance(self):
        self.assertNotEqual(_soft_kill_dispatch().status, "advance")

    def test_dispatch_explains_the_timeout(self):
        self.assertTrue(_soft_kill_dispatch().reason)


class TestBuildOutputSoftKill(unittest.TestCase):
    """End-to-end: a soft-killed run persists the ``soft_kill`` error dispatch."""

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

    def test_soft_kill_is_emitted_as_the_dispatch_status(self):
        state = self._build(_timed_out())["workflow_state"]
        self.assertIsNotNone(state.dispatch)
        self.assertEqual(state.dispatch.status, "soft_kill")

    def test_soft_kill_overrides_agent_emitted_advance_dispatch(self):
        # The wind-down finished with its own AGENT_RUN_CONFIG advance
        # sentinel — the grace-timeout override must still win.
        answer = (
            "**Work completed**: X\n"
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        )
        state = self._build(
            _timed_out(events=[_assistant_answer(answer)], wind_down_completed=True)
        )["workflow_state"]
        self.assertEqual(state.dispatch.status, "soft_kill")

    def test_agent_advance_dispatch_survives_when_run_did_not_time_out(self):
        # Control: the same sentinel really resolves to advance, so the test
        # above exercises the override rather than an unparsed sentinel.
        answer = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        conv = _make_conversation(
            events=[_assistant_answer(answer)],
            run_outcome=RunOutcome(status=OutcomeStatus.SUCCESS),
        )
        state = self._build(conv)["workflow_state"]
        self.assertEqual(state.dispatch.status, "advance")

    def test_gate_step_holds_soft_kill_without_human_resume(self):
        self._write_gated_workflow("gated")
        with patch.dict(os.environ, {"WORKFLOW_NAME": "gated"}, clear=False):
            state = self._build(_timed_out())["workflow_state"]
        self.assertIsNotNone(state.dispatch)
        self.assertEqual(state.dispatch.status, "hold")

    def test_gate_step_keeps_soft_kill_on_human_resume(self):
        self._write_gated_workflow("gated")
        with patch.dict(
            os.environ,
            {"WORKFLOW_NAME": "gated", "HUMAN_RESUME_INPUT": "/nebuko resume"},
            clear=False,
        ):
            state = self._build(_timed_out())["workflow_state"]
        self.assertIsNotNone(state.dispatch)
        self.assertEqual(state.dispatch.status, "soft_kill")

    def _write_gated_workflow(self, name):
        path = os.path.join(self._tmp.name, f"{name}.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"name: {name}\n"
                f'description: "test workflow"\n'
                f"trigger:\n  agents: [genius]\n"
                f"steps:\n  - name: implement\n    agent: dev\n"
                f"    human_gate: true\n    next: review\n"
            )


if __name__ == "__main__":
    unittest.main()
