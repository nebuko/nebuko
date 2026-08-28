"""Tests for the supervised run orchestration in run_agent (issue #489).

The Conversation is fully mocked; only the orchestration logic (watchdog
arming, arun invocation, wind-down trigger, outcome attachment) is exercised.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_script.features.run_agent.run_agent import (
    _perform_wind_down,
    _run_supervised,
    run_agent,
)
from agent_script.features.run_agent.run_outcome import (
    OutcomeStatus,
    RunOutcome,
)


def _make_conversation(status_after="finished"):
    """Build a mock conversation whose arun flips status when awaited."""
    convo = MagicMock()

    async def _arun():
        convo.execution_status = SimpleNamespace(value=status_after)
        return None

    convo.arun = _arun
    convo.interrupt = MagicMock()
    convo.send_message = MagicMock()
    return convo


class TestRunSupervised(unittest.TestCase):
    def test_completes_and_returns_watchdog(self):
        convo = _make_conversation("finished")
        watchdog, error = _run_supervised(convo, 60.0, "Main run")
        self.assertFalse(watchdog.fired)
        self.assertIsNone(error)
        self.assertFalse(convo.interrupt.called)

    def test_exception_in_arun_is_captured_not_raised(self):
        convo = MagicMock()

        async def _boom():
            raise RuntimeError("llm exploded")

        convo.arun = _boom
        watchdog, error = _run_supervised(convo, 60.0, "Main run")
        self.assertFalse(watchdog.fired)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(str(error), "llm exploded")


class TestPerformWindDown(unittest.TestCase):
    def test_sends_prompt_and_runs_second_pass(self):
        convo = _make_conversation("finished")
        budget = SimpleNamespace(wind_down_timeout_s=60.0)
        completed = _perform_wind_down(convo, budget)
        self.assertTrue(completed)
        self.assertEqual(convo.send_message.call_count, 1)
        sent_prompt = convo.send_message.call_args.args[0]
        self.assertIn("SOFT TIMEOUT", sent_prompt)

    def test_second_budget_trip_reports_incomplete(self):
        convo = _make_conversation("paused")
        real_supervised = __import__(
            "agent_script.features.run_agent.run_agent", fromlist=["x"]
        )._run_supervised

        def fake_supervised(c, budget_s, label):
            # Simulate the wind-down budget also tripping.
            wd = __import__("agent_script.features.run_agent.run_watchdog", fromlist=["x"]).RunWatchdog(
                50.0, lambda: None
            )
            wd.fired = True
            return wd, None

        with patch(
            "agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ):
            budget = SimpleNamespace(wind_down_timeout_s=60.0)
            completed = _perform_wind_down(convo, budget)
        self.assertFalse(completed)
        del real_supervised


class TestRunAgentIntegration(unittest.TestCase):
    """Full run_agent flow with a mocked Conversation factory."""

    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_success_path_no_wind_down(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        mock_conversation_cls.return_value = convo

        with patch("agent_script.features.run_agent.run_agent.get_default_condenser", return_value=None):
            result = run_agent(llm_config={}, prompt="do the thing")
        self.assertIs(result, convo)
        self.assertIsInstance(convo.run_outcome, RunOutcome)
        self.assertIs(convo.run_outcome.status, OutcomeStatus.SUCCESS)
        self.assertFalse(convo.run_outcome.soft_timeout_fired)
        # Wind-down prompt must NOT be sent on success.
        self.assertEqual(convo.send_message.call_count, 1)

    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_watchdog_trip_triggers_wind_down(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        main_convo = _make_conversation("paused")
        mock_conversation_cls.return_value = main_convo

        from agent_script.features.run_agent.run_watchdog import RunWatchdog

        calls = {"n": 0}

        def fake_supervised(convo, budget_s, label):
            calls["n"] += 1
            if calls["n"] == 1:
                # Main pass: simulate the watchdog tripping mid-flight.
                wd = RunWatchdog(50.0, lambda: None)
                wd.fired = True
                return wd, None
            # Wind-down pass: completes within its budget.
            return RunWatchdog(50.0, lambda: None), None

        with patch(
            "agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ), patch(
            "agent_script.features.run_agent.run_agent.get_default_condenser",
            return_value=None,
        ):
            result = run_agent(llm_config={}, prompt="do the thing")
        self.assertIs(result, main_convo)
        self.assertIs(
            main_convo.run_outcome.status, OutcomeStatus.GRACEFULLY_TIMED_OUT
        )
        self.assertTrue(main_convo.run_outcome.soft_timeout_fired)
        self.assertTrue(main_convo.run_outcome.wind_down_completed)
        # Two messages sent: original prompt + wind-down prompt.
        self.assertEqual(main_convo.send_message.call_count, 2)
        self.assertIn(
            "SOFT TIMEOUT", main_convo.send_message.call_args_list[1].args[0]
        )
        # Exactly two supervised passes — no recursion.
        self.assertEqual(calls["n"], 2)

    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_main_run_crash_is_failure_not_success(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        # A non-cancellation exception escaping arun() must be classified
        # as FAILURE even if the conversation status looks benign.
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        mock_conversation_cls.return_value = convo

        def fake_supervised(c, budget_s, label):
            wd = __import__(
                "agent_script.features.run_agent.run_watchdog", fromlist=["x"]
            ).RunWatchdog(50.0, lambda: None)
            return wd, RuntimeError("llm exploded")

        with patch(
            "agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ), patch(
            "agent_script.features.run_agent.run_agent.get_default_condenser",
            return_value=None,
        ):
            result = run_agent(llm_config={}, prompt="do the thing")
        self.assertIs(result, convo)
        self.assertIs(convo.run_outcome.status, OutcomeStatus.FAILURE)
        # No wind-down on a crash — only the original prompt is sent.
        self.assertEqual(convo.send_message.call_count, 1)


if __name__ == "__main__":
    unittest.main()
