"""Tests for the supervised run orchestration in run_agent (issue #489).

The Conversation is fully mocked; only the orchestration logic (watchdog
arming, arun invocation, wind-down trigger, outcome attachment) is exercised.
"""

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openhands.sdk.llm.exceptions import LLMServiceUnavailableError

from agent_script.features.run_agent.run_agent import (
    _discard_token,
    _perform_wind_down,
    _resolve_security_policy,
    _run_supervised,
    run_agent,
)
from agent_script.features.run_agent.run_failure import RunFailureKind
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
    def test_streaming_token_sink_wired(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        """The sink keeps LLM.generate on the streaming path (#775)."""
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        mock_conversation_cls.return_value = _make_conversation("finished")

        with patch(
            "agent_script.features.run_agent.run_agent.get_default_condenser",
            return_value=None,
        ):
            run_agent(llm_config={}, prompt="do the thing")

        callbacks = mock_conversation_cls.call_args.kwargs["token_callbacks"]
        self.assertEqual(callbacks, [_discard_token])

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

    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_unreachable_provider_failure_is_modelled_on_outcome(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        # An unreachable provider escapes arun() as a typed connectivity
        # error; the run must record it (issue #668) so build_output can
        # print a human-readable cause instead of the opaque sentinel.
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        mock_conversation_cls.return_value = convo

        def fake_supervised(c, budget_s, label):
            wd = __import__(
                "agent_script.features.run_agent.run_watchdog", fromlist=["x"]
            ).RunWatchdog(50.0, lambda: None)
            return wd, LLMServiceUnavailableError(
                "Cannot connect to host 192.168.1.64:31728"
            )

        with patch(
            "agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ), patch(
            "agent_script.features.run_agent.run_agent.get_default_condenser",
            return_value=None,
        ):
            run_agent(llm_config={}, prompt="do the thing")
        self.assertIs(convo.run_outcome.status, OutcomeStatus.FAILURE)
        self.assertIsNotNone(convo.run_outcome.failure)
        self.assertIs(
            convo.run_outcome.failure.kind, RunFailureKind.LLM_UNREACHABLE
        )

    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_recall_fires_records_pre_recall_event_offset(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        """When a recall pass fires, the pre-recall event count is pinned on
        RunOutcome so the capture layer can exclude the recall turn's text
        from latest-answer selection (issue #558)."""
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        convo.state.events = [SimpleNamespace(id=f"ev-{i}") for i in range(5)]
        mock_conversation_cls.return_value = convo

        from agent_script.features.run_agent.run_watchdog import RunWatchdog

        # Both the main pass and the recall pass complete within budget.
        def fake_supervised(c, budget_s, label):
            return RunWatchdog(50.0, lambda: None), None

        with patch(
            "agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ), patch(
            "agent_script.features.run_agent.run_agent.get_default_condenser",
            return_value=None,
        ), patch(
            "agent_script.features.run_agent.run_agent.has_dispatch_decision",
            # No decision at end of main run (recall fires), then the model
            # supplies it on the recall turn (second check stops the loop).
            side_effect=[False, True],
        ):
            result = run_agent(
                llm_config={},
                prompt="do the thing",
                agent_cfg=SimpleNamespace(tools=["finish", "dispatch_next"]),
            )
        self.assertIs(result, convo)
        self.assertEqual(convo.run_outcome.dispatch_recall_count, 1)
        # Offset pinned at the pre-recall event count — the index of the
        # first engine-injected recall event.
        self.assertEqual(convo.run_outcome.dispatch_recall_event_offset, 5)

    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_no_recall_keeps_event_offset_none(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        """A dispatch decision already present means zero recall turns and
        the offset stays None — the capture path must stay byte-identical
        to the legacy behavior (#558)."""
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        convo.state.events = [SimpleNamespace(id=f"ev-{i}") for i in range(3)]
        mock_conversation_cls.return_value = convo

        from agent_script.features.run_agent.run_watchdog import RunWatchdog

        with patch(
            "agent_script.features.run_agent.run_agent._run_supervised",
            return_value=(RunWatchdog(50.0, lambda: None), None),
        ), patch(
            "agent_script.features.run_agent.run_agent.get_default_condenser",
            return_value=None,
        ), patch(
            "agent_script.features.run_agent.run_agent.has_dispatch_decision",
            return_value=True,
        ):
            result = run_agent(
                llm_config={},
                prompt="do the thing",
                agent_cfg=SimpleNamespace(tools=["finish", "dispatch_next"]),
            )
        self.assertIs(result, convo)
        self.assertEqual(convo.run_outcome.dispatch_recall_count, 0)
        self.assertIsNone(convo.run_outcome.dispatch_recall_event_offset)


class TestResolveSecurityPolicy(unittest.TestCase):
    """Skills route through the system channel; SDK default as fallback (#510)."""

    def test_skill_file_routed_to_system_channel(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as f:
            f.write("# Stub-Only Mode\nsentinel skill text\n")
            f.flush()
            with patch("agent_script.features.run_agent.run_agent.os.environ", {
                "SKILL_CONTEXT_FILE": f.name
            }):
                self.assertEqual(_resolve_security_policy(), f.name)

    def test_env_var_unset_falls_back_to_default(self):
        with patch(
            "agent_script.features.run_agent.run_agent.os.environ", {}
        ):
            self.assertEqual(_resolve_security_policy(), "security_policy.j2")

    def test_nonexistent_path_falls_back_to_default(self):
        with patch(
            "agent_script.features.run_agent.run_agent.os.environ", {
                "SKILL_CONTEXT_FILE": "/tmp/definitely-missing-skill-context.md"
            }
        ):
            self.assertEqual(_resolve_security_policy(), "security_policy.j2")

    def test_empty_file_falls_back_to_default(self):
        with tempfile.NamedTemporaryFile(mode="w") as f:
            f.flush()
            with patch("agent_script.features.run_agent.run_agent.os.environ", {
                "SKILL_CONTEXT_FILE": f.name
            }):
                self.assertEqual(_resolve_security_policy(), "security_policy.j2")


class TestRunAgentSystemChannelIntegration(unittest.TestCase):
    """run_agent passes the resolved policy to the Agent constructor."""

    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_agent_receives_skill_file_as_security_policy(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        mock_conversation_cls.return_value = convo
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as f:
            f.write("skill content\n")
            f.flush()
            with patch(
                "agent_script.features.run_agent.run_agent.os.environ", {
                    "SKILL_CONTEXT_FILE": f.name,
                    "ROLE_NAME": "dev",
                }
            ), patch(
                "agent_script.features.run_agent.run_agent.get_default_condenser",
                return_value=None,
            ):
                run_agent(llm_config={}, prompt="do the thing")
        self.assertEqual(
            mock_agent.call_args.kwargs["security_policy_filename"], f.name
        )

    @patch("agent_script.features.run_agent.run_agent.Conversation")
    @patch("agent_script.features.run_agent.run_agent.Agent")
    @patch("agent_script.features.run_agent.run_agent.LLM")
    @patch("agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_agent_keeps_default_policy_without_skills(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        mock_conversation_cls.return_value = convo
        with patch(
            "agent_script.features.run_agent.run_agent.os.environ",
            {"ROLE_NAME": "dev"},
        ), patch(
            "agent_script.features.run_agent.run_agent.get_default_condenser",
            return_value=None,
        ):
            run_agent(llm_config={}, prompt="do the thing")
        self.assertEqual(
            mock_agent.call_args.kwargs["security_policy_filename"],
            "security_policy.j2",
        )


if __name__ == "__main__":
    unittest.main()
