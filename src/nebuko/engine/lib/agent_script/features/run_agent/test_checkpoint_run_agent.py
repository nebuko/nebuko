"""Tests for the checkpoint delivery wiring in run_agent (issue #966).

The Conversation is mocked; only the orchestration logic (scheduler arming,
queued delivery, skip-when-finished, wind-down trigger) is exercised.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_script.features.run_agent import run_agent as run_agent_module
from agent_script.features.run_agent.run_agent import run_agent
from agent_script.features.run_agent.run_outcome import OutcomeStatus


def _deliver_checkpoint(convo, index):
    return run_agent_module._deliver_checkpoint(convo, index)


def _run_with_checkpoints(convo, budget):
    return run_agent_module._run_with_checkpoints(convo, budget)


def _make_conversation(status_after="finished"):
    """Build a mock conversation whose arun flips status when awaited."""
    convo = MagicMock()

    async def _arun():
        convo.execution_status = SimpleNamespace(value=status_after)
        return None

    convo.arun = _arun
    convo.interrupt = MagicMock()
    convo.send_message = MagicMock()
    convo.id = "test-conversation"
    return convo


class TestDeliverCheckpoint(unittest.TestCase):
    """Given a checkpoint index, when it fires, delivery matches the schedule."""

    def test_finished_run_skips_delivery(self):
        convo = _make_conversation("finished")
        delivered = _deliver_checkpoint(convo, 1)
        self.assertFalse(delivered)
        convo.send_message.assert_not_called()
        convo.interrupt.assert_not_called()

    def test_checkpoint_1_queues_message_without_interrupt(self):
        convo = _make_conversation("running")
        delivered = _deliver_checkpoint(convo, 1)
        self.assertTrue(delivered)
        self.assertEqual(convo.send_message.call_count, 1)
        convo.interrupt.assert_not_called()

    def test_checkpoint_2_queues_message_without_interrupt(self):
        convo = _make_conversation("running")
        delivered = _deliver_checkpoint(convo, 2)
        self.assertTrue(delivered)
        self.assertEqual(convo.send_message.call_count, 1)
        convo.interrupt.assert_not_called()

    def test_checkpoint_3_interrupts(self):
        convo = _make_conversation("running")
        delivered = _deliver_checkpoint(convo, 3)
        self.assertTrue(delivered)
        convo.interrupt.assert_called_once()


class TestRunWithCheckpoints(unittest.TestCase):
    """Given a mocked conversation, when the run is supervised, checkpoints fire."""

    @patch.object(run_agent_module, "_deliver_checkpoint")
    def test_scheduler_fires_all_checkpoints_on_long_run(self, mock_deliver):
        convo = _make_conversation("finished")
        budget = SimpleNamespace(soft_timeout_s=0.3, wind_down_timeout_s=30.0)
        mock_deliver.return_value = True

        final_fired, error = _run_with_checkpoints(convo, budget)
        self.assertIsNone(error)
        self.assertTrue(final_fired)
        self.assertEqual(mock_deliver.call_count, 3)

    @patch.object(run_agent_module, "_deliver_checkpoint")
    def test_fast_run_fires_no_checkpoint(self, mock_deliver):
        convo = _make_conversation("finished")
        budget = SimpleNamespace(soft_timeout_s=60.0, wind_down_timeout_s=30.0)

        final_fired, error = _run_with_checkpoints(convo, budget)
        self.assertIsNone(error)
        self.assertFalse(final_fired)
        mock_deliver.assert_not_called()


class TestCheckpointRunIntegration(unittest.TestCase):
    """Full run_agent flow with checkpoints and a mocked Conversation factory."""

    @patch("agent_script.features.run_agent.run_agent.HandoffTracker")
    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_soft_kill_winds_down_with_final_handoff_prompt(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls, mock_tracker
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=0.2, wind_down_timeout_s=30.0
        )
        main_convo = _make_conversation("paused")
        mock_conversation_cls.return_value = main_convo
        mock_tracker.take.return_value = (1, 2)

        with patch(
            "agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            result = run_agent(llm_config={}, prompt="do the thing")

        self.assertIs(result, main_convo)
        self.assertIs(
            main_convo.run_outcome.status, OutcomeStatus.GRACEFULLY_TIMED_OUT
        )
        self.assertTrue(main_convo.run_outcome.soft_timeout_fired)
        self.assertTrue(main_convo.run_outcome.wind_down_completed)
        # Checkpoint 3 fired: the final handoff prompt was sent for the
        # wind-down pass.
        prompts = [c.args[0] for c in main_convo.send_message.call_args_list]
        from agent_script.features.run_agent.checkpoint_prompt import (
            FINAL_HANDOFF_PROMPT,
        )

        self.assertIn(FINAL_HANDOFF_PROMPT, prompts)
        # Drift signal: the tracker was consulted for the run.
        mock_tracker.take.assert_called_once_with("test-conversation")
        self.assertEqual(main_convo.run_outcome.checkpoint_count, 3)
        self.assertEqual(main_convo.run_outcome.plan_changes_total, 2)


if __name__ == "__main__":
    unittest.main()
