"""Tests for the supervised run orchestration in run_agent (issue #489).

The Conversation is fully mocked; only the orchestration logic (watchdog
arming, arun invocation, wind-down trigger, outcome attachment) is exercised.
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openhands.sdk.llm.exceptions import LLMServiceUnavailableError

from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
    _SYSTEM_PROMPT,
    _build_mcp_servers,
    _discard_token,
    _perform_wind_down,
    _resolve_mcp_env,
    _run_supervised,
    run_agent,
)
from nebuko.engine.lib.agent_script.features.run_agent.run_failure import (
    ProviderFailure,
    RunFailureKind,
)
from nebuko.engine.lib.agent_script.features.run_agent.run_outcome import (
    OutcomeStatus,
    RunOutcome,
)
from nebuko.engine.harness.engine_tools import FinalizeSink
from nebuko.engine.lib.agent_script.features.run_agent.run_watchdog import RunWatchdog


# SDK registry sections the static prompt used to carry. Issue #943 removes all
# of them; these pins keep them from coming back.
_SDK_SECTION_MARKERS = (
    "<MEMORY>",
    "<SECURITY>",
    "<SECURITY_RISK_ASSESSMENT>",
    "<EXTERNAL_SERVICES>",
    "<PROCESS_MANAGEMENT>",
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


class _FakeHarnessSession:
    """Minimal ``HarnessSession``: records turns, can fail chosen turns."""

    def __init__(self, *, sink=None, fail_on=()) -> None:
        from nebuko.engine.harness.engine_tools import FinalizeSink

        self.sink = sink if sink is not None else FinalizeSink()
        self.messages: list[str] = []
        self.runs: list[float | None] = []
        self.on_run = None
        self._fail_on = set(fail_on)

    def send_message(self, text: str) -> None:
        self.messages.append(text)

    def run(self, timeout_s: float | None = None) -> None:
        self.runs.append(timeout_s)
        if len(self.runs) in self._fail_on:
            raise TimeoutError("turn budget")
        if self.on_run is not None:
            self.on_run(len(self.runs))

    def close(self) -> None:
        pass

    def on_event(self, callback) -> None:
        pass


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
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent", fromlist=["x"]
        )._run_supervised

        def fake_supervised(c, budget_s, label):
            # Simulate the wind-down budget also tripping.
            wd = __import__("nebuko.engine.lib.agent_script.features.run_agent.run_watchdog", fromlist=["x"]).RunWatchdog(
                50.0, lambda: None
            )
            wd.fired = True
            return wd, None

        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ):
            budget = SimpleNamespace(wind_down_timeout_s=60.0)
            completed = _perform_wind_down(convo, budget)
        self.assertFalse(completed)
        del real_supervised


class TestRunAgentIntegration(unittest.TestCase):
    """Full run_agent flow with a mocked Conversation factory."""

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_success_path_no_wind_down(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        mock_conversation_cls.return_value = convo

        with patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser", return_value=None):
            result = run_agent(llm_config={}, prompt="do the thing")
        self.assertIs(result, convo)
        self.assertIsInstance(convo.run_outcome, RunOutcome)
        self.assertIs(convo.run_outcome.status, OutcomeStatus.SUCCESS)
        self.assertFalse(convo.run_outcome.soft_timeout_fired)
        # The work pass sends the prompt once; the fake conversation records
        # no finalize verdict, so the bounded recall fires (2 turns, issue
        # #1033 moved the terminal tool to ``finalize``).
        recalls = convo.run_outcome.dispatch_recall_count
        self.assertEqual(convo.send_message.call_count, 1 + recalls)
        # The wind-down prompt must NOT be sent on success.
        for call in convo.send_message.call_args_list:
            self.assertNotIn("SOFT TIMEOUT", call.args[0])

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_streaming_token_sink_wired(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        """The sink keeps LLM.generate on the streaming path (#775)."""
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        mock_conversation_cls.return_value = _make_conversation("finished")

        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            run_agent(llm_config={}, prompt="do the thing")

        callbacks = mock_conversation_cls.call_args.kwargs["token_callbacks"]
        # [0] keeps the streaming path live (#775); [1] feeds the token
        # telemetry collector added in #829.
        self.assertEqual(len(callbacks), 2)
        self.assertIs(callbacks[0], _discard_token)
        self.assertEqual(callbacks[1].__name__, "on_token")
        # The same collector receives the SDK's retry attempts (#1352).
        listener = mock_llm.call_args.kwargs["retry_listener"]
        self.assertEqual(listener.__name__, "on_retry")
        self.assertIs(listener.__self__, callbacks[1].__self__)

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_watchdog_trip_triggers_wind_down(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        main_convo = _make_conversation("paused")
        mock_conversation_cls.return_value = main_convo

        from nebuko.engine.lib.agent_script.features.run_agent.run_watchdog import RunWatchdog

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
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ), patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            result = run_agent(llm_config={}, prompt="do the thing")
        self.assertIs(result, main_convo)
        self.assertIs(
            main_convo.run_outcome.status, OutcomeStatus.GRACEFULLY_TIMED_OUT
        )
        self.assertTrue(main_convo.run_outcome.soft_timeout_fired)
        self.assertTrue(main_convo.run_outcome.wind_down_completed)
        # Messages sent: original prompt + wind-down prompt, plus one turn per
        # bounded recall pass (the fake conversation records no verdict).
        recalls = main_convo.run_outcome.dispatch_recall_count
        self.assertEqual(main_convo.send_message.call_count, 2 + recalls)
        self.assertIn(
            "SOFT TIMEOUT", main_convo.send_message.call_args_list[1].args[0]
        )
        # No recursion: main + wind-down passes, plus one per recall pass.
        self.assertEqual(calls["n"], 2 + recalls)

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
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
                "nebuko.engine.lib.agent_script.features.run_agent.run_watchdog", fromlist=["x"]
            ).RunWatchdog(50.0, lambda: None)
            return wd, RuntimeError("llm exploded")

        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ), patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            result = run_agent(llm_config={}, prompt="do the thing")
        self.assertIs(result, convo)
        self.assertIs(convo.run_outcome.status, OutcomeStatus.FAILURE)
        # No wind-down on a crash — only the original prompt is sent.
        self.assertEqual(convo.send_message.call_count, 1)

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_unreachable_provider_failure_stops_the_run(self, mock_budget, mock_llm, mock_agent, mock_conversation_cls):
        # An unreachable provider escapes arun() as a typed connectivity
        # error; the run must stop there (issue #1322) so the engine reports
        # the real cause instead of a synthesized comment.
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        convo = _make_conversation("finished")
        mock_conversation_cls.return_value = convo

        def fake_supervised(c, budget_s, label):
            wd = __import__(
                "nebuko.engine.lib.agent_script.features.run_agent.run_watchdog", fromlist=["x"]
            ).RunWatchdog(50.0, lambda: None)
            return wd, LLMServiceUnavailableError(
                "Cannot connect to host 192.168.1.64:31728"
            )

        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ), patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            with self.assertRaises(ProviderFailure) as raised:
                run_agent(llm_config={}, prompt="do the thing")
        self.assertIn("Cannot connect", raised.exception.provider_error)
        self.assertIs(raised.exception.agent_phase, "agent")

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
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

        from nebuko.engine.lib.agent_script.features.run_agent.run_watchdog import RunWatchdog

        # Both the main pass and the recall pass complete within budget.
        def fake_supervised(c, budget_s, label):
            return RunWatchdog(50.0, lambda: None), None

        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent._run_supervised",
            side_effect=fake_supervised,
        ), patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ), patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.has_dispatch_decision",
            # No decision at end of main run (recall fires), then the model
            # supplies it on the recall turn (second check stops the loop).
            side_effect=[False, True],
        ):
            result = run_agent(
                llm_config={},
                prompt="do the thing",
                agent_cfg=SimpleNamespace(tools=["finish", "finalize"]),
            )
        self.assertIs(result, convo)
        self.assertEqual(convo.run_outcome.dispatch_recall_count, 1)
        # Offset pinned at the pre-recall event count — the index of the
        # first engine-injected recall event.
        self.assertEqual(convo.run_outcome.dispatch_recall_event_offset, 5)

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
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

        from nebuko.engine.lib.agent_script.features.run_agent.run_watchdog import RunWatchdog

        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent._run_supervised",
            return_value=(RunWatchdog(50.0, lambda: None), None),
        ), patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ), patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.has_dispatch_decision",
            return_value=True,
        ):
            result = run_agent(
                llm_config={},
                prompt="do the thing",
                agent_cfg=SimpleNamespace(tools=["finish", "finalize"]),
            )
        self.assertIs(result, convo)
        self.assertEqual(convo.run_outcome.dispatch_recall_count, 0)
        self.assertIsNone(convo.run_outcome.dispatch_recall_event_offset)


class TestRunAgentSystemPromptWiring(unittest.TestCase):
    """run_agent hands its own static prompt to the Agent constructor (#943)."""

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_agent_receives_nebuko_static_prompt(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        mock_conversation_cls.return_value = _make_conversation("finished")
        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            run_agent(llm_config={}, prompt="do the thing")

        self.assertEqual(
            mock_agent.call_args.kwargs["system_prompt"], _SYSTEM_PROMPT
        )

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_no_legacy_prompt_levers_reach_the_agent(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        """`persona`/`security_policy_filename`/`system_prompt_kwargs` are gone.

        All three fed the registry the inline prompt now bypasses, so passing
        them again would be a silent no-op at best.
        """
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        mock_conversation_cls.return_value = _make_conversation("finished")
        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            run_agent(llm_config={}, prompt="do the thing")

        kwargs = mock_agent.call_args.kwargs
        for lever in ("persona", "security_policy_filename", "system_prompt_kwargs"):
            self.assertNotIn(lever, kwargs)


class TestSystemPromptOverride(unittest.TestCase):
    """The SDK's built-in prompt is replaced wholesale (issues #938, #943).

    The inline ``system_prompt`` escape hatch is the only lever that reaches the
    three unguarded registry sections (``memory``, ``external_services``,
    ``process_management``); ``persona`` deliberately kept them.
    """

    def test_prompt_is_non_blank(self):
        self.assertTrue(_SYSTEM_PROMPT.strip())

    def test_prompt_carries_no_sdk_section_markers(self):
        for marker in _SDK_SECTION_MARKERS:
            self.assertNotIn(marker, _SYSTEM_PROMPT)
        self.assertNotIn("<SOUL>", _SYSTEM_PROMPT)
        self.assertNotIn("<ROLE>", _SYSTEM_PROMPT)

    def test_prompt_defers_control_to_role_instructions(self):
        # The last line routes the agent to the control tools, which live in the
        # role prompt + the dynamic tier (SHARED_EXIT_RULES).
        self.assertIn(
            "Finish through the control tools described in your role instructions",
            _SYSTEM_PROMPT,
        )


class TestSdkStaticPromptRegression(unittest.TestCase):
    """The SDK's own static sections must never reach the model again (#943)."""

    def test_real_agent_static_prompt_is_nebuko_prompt_only(self):
        from openhands.sdk import Agent, LLM

        agent = Agent(
            llm=LLM(model="gpt-4o-mini"),
            system_prompt=_SYSTEM_PROMPT,
        )
        static = agent.static_system_message
        for marker in _SDK_SECTION_MARKERS:
            self.assertNotIn(marker, static)
        # The SDK appends the loaded tools' prompt guidance after the inline
        # prompt; the prompt itself is the whole static head.
        self.assertIn(_SYSTEM_PROMPT.strip("\n"), static)


class TestMCPServerWiring(unittest.TestCase):
    """``mcp_servers`` config → SDK server handles (issue #823)."""

    def _spec(self, name="brave-search", env=None, args=None):
        from nebuko.engine.lib.config import MCPServerConfig

        return MCPServerConfig(
            name=name,
            command="npx",
            args=args if args is not None else ["-y", "@brave/brave-search-mcp-server@2.0.1"],
            env=env if env is not None else {"BRAVE_API_KEY": "BRAVE_API_KEY"},
        )

    def test_resolve_mcp_env_returns_values_and_missing(self):
        with patch.dict(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.os.environ",
            {"BRAVE_API_KEY": "secret-value"},
            clear=True,
        ):
            resolved, missing = _resolve_mcp_env(
                {"BRAVE_API_KEY": "BRAVE_API_KEY", "OTHER": "UNSET_VAR"}
            )
        self.assertEqual(resolved, {"BRAVE_API_KEY": "secret-value"})
        self.assertEqual(missing, ["UNSET_VAR"])

    def test_build_mcp_servers_none_config_is_empty(self):
        self.assertEqual(_build_mcp_servers(None), {})

    def test_build_mcp_servers_no_servers_is_empty(self):
        self.assertEqual(
            _build_mcp_servers(SimpleNamespace(mcp_servers=[])), {}
        )

    def test_build_mcp_servers_resolves_env_into_handle(self):
        cfg = SimpleNamespace(mcp_servers=[self._spec()])
        with patch.dict(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.os.environ",
            {"BRAVE_API_KEY": "secret-value"},
            clear=True,
        ):
            servers = _build_mcp_servers(cfg)

        self.assertEqual(list(servers), ["brave-search"])
        server = servers["brave-search"]
        self.assertEqual(server.command, "npx")
        self.assertIn("@brave/brave-search-mcp-server@2.0.1", server.args)
        self.assertEqual(
            server.env["BRAVE_API_KEY"].get_secret_value(), "secret-value"
        )
        # Resolved values are frozen literal so the SDK does not re-expand them.
        self.assertTrue(server.literal_values)

    def test_missing_env_skips_only_that_server(self):
        cfg = SimpleNamespace(
            mcp_servers=[
                self._spec(name="needs-key", env={"API_KEY": "ABSENT_VAR"}),
                self._spec(name="keyless", env={}),
            ]
        )
        with patch.dict(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.os.environ", {}, clear=True
        ):
            servers = _build_mcp_servers(cfg)
        self.assertEqual(list(servers), ["keyless"])

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget")
    def test_run_agent_attaches_mcp_config(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        mock_conversation_cls.return_value = _make_conversation("finished")

        with patch.dict(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.os.environ",
            {"ROLE_NAME": "context", "BRAVE_API_KEY": "secret-value"},
            clear=True,
        ), patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            run_agent(
                llm_config={},
                prompt="do the thing",
                agent_cfg=SimpleNamespace(
                    tools=["finish"], mcp_servers=[self._spec()]
                ),
            )

        mcp_config = mock_agent.call_args.kwargs["mcp_config"]
        self.assertEqual(list(mcp_config), ["brave-search"])
        self.assertEqual(
            mcp_config["brave-search"].env["BRAVE_API_KEY"].get_secret_value(),
            "secret-value",
        )


class TestHarnessRunPath(unittest.TestCase):
    """The non-OpenHands run path keeps the timeout and recall guarantees."""

    def _run(self, session, capabilities=None):
        from types import SimpleNamespace

        from nebuko.engine.harness.claude_code import ClaudeCodeHarness
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _run_harness_agent,
        )

        harness = ClaudeCodeHarness()
        conversation = SimpleNamespace(
            harness_session=session, state=SimpleNamespace(events=[])
        )
        harness.build_conversation = lambda **kwargs: conversation
        if capabilities is not None:
            harness.capabilities = lambda: capabilities
        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget",
            return_value=SimpleNamespace(
                soft_timeout_s=60.0, wind_down_timeout_s=30.0
            ),
        ):
            return _run_harness_agent(
                harness,
                role_name="dev",
                agent_cfg=None,
                prompt="do the thing",
                cwd="/tmp/repo",
            )

    def test_soft_timeout_runs_a_wind_down_turn(self):
        session = _FakeHarnessSession(fail_on={1})
        conversation = self._run(session)

        self.assertEqual(
            conversation.run_outcome.status, OutcomeStatus.GRACEFULLY_TIMED_OUT
        )
        self.assertTrue(conversation.run_outcome.soft_timeout_fired)
        self.assertTrue(conversation.run_outcome.wind_down_completed)
        # The wind-down prompt is the second message; the remaining runs are
        # the bounded recall passes (parity with the OpenHands path).
        self.assertIn("WIND-DOWN", session.messages[1])
        self.assertEqual(session.runs[1], 30.0)

    def test_wind_down_failure_keeps_the_timeout_outcome(self):
        session = _FakeHarnessSession(fail_on={1, 2})
        conversation = self._run(session)

        self.assertEqual(
            conversation.run_outcome.status, OutcomeStatus.GRACEFULLY_TIMED_OUT
        )
        self.assertFalse(conversation.run_outcome.wind_down_completed)

    def test_missing_sink_outcome_triggers_bounded_recall(self):
        sink = FinalizeSink()
        session = _FakeHarnessSession(sink=sink)

        def _record(turn: int) -> None:
            # The work turn records nothing; the recall turn supplies it.
            if turn > 1:
                sink.record(
                    {"report": "r", "turn_outcome": {"appreciation": "approved"}}
                )

        session.on_run = _record
        conversation = self._run(session)

        self.assertEqual(conversation.run_outcome.dispatch_recall_count, 1)
        self.assertEqual(conversation.run_outcome.dispatch_recall_event_offset, 0)

    def test_recorded_outcome_skips_recall(self):
        sink = FinalizeSink()
        sink.record({"report": "r", "turn_outcome": {"appreciation": "approved"}})
        conversation = self._run(_FakeHarnessSession(sink=sink))

        self.assertEqual(conversation.run_outcome.dispatch_recall_count, 0)
        self.assertIsNone(conversation.run_outcome.dispatch_recall_event_offset)

    def test_watchdog_interrupts_rather_than_closing_the_session(self):
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _interrupt_hook,
        )

        session = _FakeHarnessSession()
        session.interrupt = MagicMock()
        session.close = MagicMock()

        _interrupt_hook(session)()

        session.interrupt.assert_called_once_with()
        session.close.assert_not_called()

    def test_watchdog_falls_back_to_close_without_interrupt(self):
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _interrupt_hook,
        )

        session = _FakeHarnessSession()
        session.close = MagicMock()

        _interrupt_hook(session)()

        session.close.assert_called_once_with()


class TestParseContextBodiesEnv(unittest.TestCase):
    """``CONTEXT_BODIES`` carries a temp-file *path*, not inline JSON (#1193)."""

    def setUp(self) -> None:
        import json
        import os
        import tempfile

        self._json = json
        self._os = os
        self._tempfile = tempfile
        self._prior = os.environ.get("CONTEXT_BODIES")
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._prior is None:
            self._os.environ.pop("CONTEXT_BODIES", None)
        else:
            self._os.environ["CONTEXT_BODIES"] = self._prior

    def test_reads_bodies_from_the_path_and_removes_the_file(self) -> None:
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _parse_context_bodies_env,
        )

        fd, path = self._tempfile.mkstemp(suffix=".json")
        with self._os.fdopen(fd, "w") as handle:
            self._json.dump({"101": "full text"}, handle)
        self._os.environ["CONTEXT_BODIES"] = path

        self.assertEqual(_parse_context_bodies_env(), {"101": "full text"})
        self.assertFalse(self._os.path.exists(path))

    def test_missing_file_degrades_to_an_empty_map(self) -> None:
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _parse_context_bodies_env,
        )

        self._os.environ["CONTEXT_BODIES"] = "/nonexistent/path.json"
        self.assertEqual(_parse_context_bodies_env(), {})


class TestClaudeCredentialGate(unittest.TestCase):
    """ADR 0017 rule 1 is enforced at run time, not only by the linter."""

    def setUp(self):
        # A bot-triggered environment (a workflow-continue run) must not leak into
        # these human-triggered cases: _continuation_from_env reads it.
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in (
            "TRIGGER_AUTHOR_LOGIN",
            "TRIGGER_AUTHOR_TYPE",
            "TRIGGER_COMMENT_HEADER",
            "CLAUDE_CREDENTIAL_OWNER",
            "WORKFLOW_LANE",
        ):
            os.environ.pop(key, None)

    @staticmethod
    def _agent(backend=None, model_path="claude-code/sonnet", token="CLAUDE_CODE_OAUTH_TOKEN"):
        ref = SimpleNamespace(
            model_path=model_path,
            provider_ref=SimpleNamespace(api_token_env=token),
        )
        return SimpleNamespace(
            backend=backend, model_ref=ref, complexity_overrides={}
        )

    def test_subscription_token_on_openhands_is_refused(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _enforce_harness_credentials,
        )

        with self.assertRaises(ClaudeCredentialError) as context:
            _enforce_harness_credentials(
                self._agent(backend=None, model_path="anthropic/claude-sonnet")
            )
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", str(context.exception))

    def test_claude_code_backend_with_a_metered_key_is_refused(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _enforce_harness_credentials,
        )

        with self.assertRaises(ClaudeCredentialError):
            _enforce_harness_credentials(
                self._agent(
                    backend="claude-code",
                    model_path="anthropic/claude-sonnet",
                    token="ANTHROPIC_API_KEY",
                )
            )

    def test_claude_code_backend_with_claude_credentials_passes(self):
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _enforce_harness_credentials,
        )

        _enforce_harness_credentials(self._agent(backend="claude-code"))

    def test_bot_continuation_on_claude_code_runs_only_with_human_started_state(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError, ContinuationContext
        from nebuko.engine.lib.agent_script.features.run_agent import run_agent as ra

        ctx = ContinuationContext(
            trigger_login="nebuko[bot]",
            trigger_type="Bot",
            trusted_bots=("nebuko[bot]",),
            comment_header="<!-- workflow-continue -->",
            started_by="alice",
        )
        agent = self._agent(backend="claude-code")
        original = ra._caller_association
        ra._caller_association = lambda login: "OWNER" if login == "alice" else None
        try:
            ra._enforce_harness_credentials(agent, continuation=ctx)
            with self.assertRaises(ClaudeCredentialError) as refused:
                ra._enforce_harness_credentials(
                    agent, continuation=ContinuationContext(**{**ctx.__dict__, "started_by": None})
                )
        finally:
            ra._caller_association = original
        self.assertIn("human-started-state", str(refused.exception))

    def test_user_repo_state_on_the_org_shared_token_is_refused(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError, ContinuationContext
        from nebuko.engine.lib.agent_script.features.run_agent import run_agent as ra

        ctx = ContinuationContext(
            trigger_login="nebuko[bot]",
            trigger_type="Bot",
            trusted_bots=("nebuko[bot]",),
            comment_header="<!-- workflow-continue -->",
            started_by="alice",
            credential_owner=None,
            lane="shared",
            previous_lane="user-repo",
            previous_credential_owner="alice",
        )
        agent = self._agent(backend="claude-code")
        original = ra._caller_association
        ra._caller_association = lambda login: "OWNER" if login == "alice" else None
        try:
            with self.assertRaises(ClaudeCredentialError) as refused:
                ra._enforce_harness_credentials(agent, continuation=ctx)
        finally:
            ra._caller_association = original
        self.assertIn("user-repo lane", str(refused.exception))
        self.assertIn("alice/nebuko-personal", str(refused.exception))

    def test_a_tier_swap_is_checked_too(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _enforce_harness_credentials,
        )

        agent = self._agent(
            backend=None, model_path="anthropic/claude-sonnet", token="ANTHROPIC_API_KEY"
        )
        agent.complexity_overrides = {
            "high": SimpleNamespace(
                model_ref=SimpleNamespace(
                    model_path="claude-code/opus",
                    provider_ref=SimpleNamespace(
                        api_token_env="CLAUDE_CODE_OAUTH_TOKEN"
                    ),
                )
            )
        }
        with self.assertRaises(ClaudeCredentialError):
            _enforce_harness_credentials(agent)

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    def test_run_agent_refuses_before_building_the_llm(self, mock_llm):
        from nebuko.engine.harness.credentials import ClaudeCredentialError

        with self.assertRaises(ClaudeCredentialError):
            run_agent(
                llm_config={},
                prompt="do the thing",
                agent_cfg=self._agent(
                    backend=None, model_path="anthropic/claude-sonnet"
                ),
            )
        mock_llm.assert_not_called()

    def test_the_model_the_llm_is_built_from_is_checked(self):
        """A ``CONFIG_MODEL`` override can swap in a claude-code model.

        The config refs are metered, so only the effective ``llm_config`` model
        reveals the violation; that is the string ``LLM(**llm_config)`` gets.
        """
        from nebuko.engine.harness.credentials import ClaudeCredentialError
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _enforce_harness_credentials,
        )

        agent = self._agent(
            backend=None, model_path="anthropic/claude-sonnet", token="ANTHROPIC_API_KEY"
        )
        with self.assertRaises(ClaudeCredentialError):
            _enforce_harness_credentials(agent, {"model": "claude-code/opus"})

    def test_the_condenser_model_is_checked(self):
        """The condenser builds a second LLM; it must not bypass the gate."""
        from nebuko.engine.harness.credentials import ClaudeCredentialError
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _enforce_harness_credentials,
        )
        from nebuko.engine.lib.config import TokenBudgetPolicy

        agent = self._agent(
            backend=None, model_path="anthropic/claude-sonnet", token="ANTHROPIC_API_KEY"
        )
        agent.token_budget = TokenBudgetPolicy(compaction_model="claude-code/opus")
        with self.assertRaises(ClaudeCredentialError):
            _enforce_harness_credentials(
                agent, {"model": "anthropic/claude-sonnet"}
            )

    def test_claude_code_backend_with_a_metered_key_and_claude_model_passes(self):
        """ADR 0017 Lane K: the binary also runs on ANTHROPIC_API_KEY."""
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _enforce_harness_credentials,
        )

        _enforce_harness_credentials(
            self._agent(backend="claude-code", token="ANTHROPIC_API_KEY"),
            {"model": "claude-code/sonnet"},
        )

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    def test_run_agent_refuses_a_smuggled_llm_config_model(self, mock_llm):
        """The refused ref is the one ``LLM(**llm_config)`` would be built from."""
        from nebuko.engine.harness.credentials import ClaudeCredentialError

        with self.assertRaises(ClaudeCredentialError):
            run_agent(
                llm_config={"model": "claude-code/opus"},
                prompt="do the thing",
                agent_cfg=self._agent(
                    backend=None,
                    model_path="anthropic/claude-sonnet",
                    token="ANTHROPIC_API_KEY",
                ),
            )
        mock_llm.assert_not_called()


class TestSubscriptionTokenIsolation(unittest.TestCase):
    """ADR 0017 A3: only the Claude Code backend may see the token.

    The workflow exports ``CLAUDE_CODE_OAUTH_TOKEN`` into the job environment
    of every comment-triggered run. The OpenHands ``terminal`` tool inherits
    ``os.environ``, so a prompt-injected agent on that backend could read and
    exfiltrate it unless the run path drops it first.
    """

    TOKEN = "sk-ant-oat-subscription-token"

    def setUp(self) -> None:
        self._prior = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = self.TOKEN
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._prior is None:
            os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        else:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = self._prior

    def test_openhands_run_scrubs_the_token_from_the_agent_environment(self):
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _drop_subscription_tokens_for,
        )

        _drop_subscription_tokens_for("openhands")

        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", os.environ)

    def test_claude_code_run_keeps_the_token_for_the_binary(self):
        from nebuko.engine.lib.agent_script.features.run_agent.run_agent import (
            _drop_subscription_tokens_for,
        )

        _drop_subscription_tokens_for("claude-code")

        self.assertEqual(os.environ["CLAUDE_CODE_OAUTH_TOKEN"], self.TOKEN)

    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Conversation")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.Agent")
    @patch("nebuko.engine.lib.agent_script.features.run_agent.run_agent.LLM")
    @patch(
        "nebuko.engine.lib.agent_script.features.run_agent.run_agent.resolve_timeout_budget"
    )
    def test_run_agent_on_openhands_never_hands_it_to_the_agent(
        self, mock_budget, mock_llm, mock_agent, mock_conversation_cls
    ):
        mock_budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        mock_conversation_cls.return_value = _make_conversation("finished")

        seen: list[str | None] = []

        def _capture(**_kwargs):
            seen.append(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
            return MagicMock()

        mock_agent.side_effect = _capture
        with patch(
            "nebuko.engine.lib.agent_script.features.run_agent.run_agent.build_condenser",
            return_value=None,
        ):
            run_agent(llm_config={}, prompt="do the thing")

        self.assertEqual(seen, [None])
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", os.environ)


if __name__ == "__main__":
    unittest.main()


class TestLaneBinding(unittest.TestCase):
    """ADR 0017 lane binding: every trigger is checked; only the starter re-baselines."""

    def setUp(self):
        import tempfile

        from nebuko.engine.lib.agent_script.features.run_agent import run_agent as ra

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in (
            "TRIGGER_AUTHOR_LOGIN", "TRIGGER_AUTHOR_TYPE", "TRIGGER_COMMENT_HEADER",
            "WORKFLOW_LANE", "CLAUDE_CREDENTIAL_OWNER", "GITHUB_EVENT_NAME",
            "GITHUB_EVENT_PATH", "GITHUB_REPOSITORY_OWNER", "TRUSTED_BOTS",
        ):
            os.environ.pop(key, None)
        os.environ["RUNNER_TEMP"] = self._tmp.name
        original = ra._caller_association
        ra._caller_association = lambda login: "OWNER" if login in {"alice", "bob"} else None
        self.addCleanup(setattr, ra, "_caller_association", original)
        self.ra = ra

    def _seed_user_repo_state(self, owner="alice"):
        from nebuko.engine.lib.workflow_state.state_codec import WorkflowStateCodec
        from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

        WorkflowStateCodec().save_prev(
            WorkflowState(
                workflow="dev-review-loop",
                started_by=owner,
                lane="user-repo",
                credential_owner=owner,
            )
        )

    def _human(self, login, header="/nebuko resume"):
        os.environ["TRIGGER_AUTHOR_LOGIN"] = login
        os.environ["TRIGGER_AUTHOR_TYPE"] = "User"
        os.environ["TRIGGER_COMMENT_HEADER"] = header

    @staticmethod
    def _agent(backend=None, model_path="claude-code/sonnet", token="CLAUDE_CODE_OAUTH_TOKEN"):
        return TestClaudeCredentialGate._agent(backend, model_path, token)

    def test_human_resume_on_user_repo_state_is_refused(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError

        self._seed_user_repo_state("alice")
        self._human("bob")
        with self.assertRaises(ClaudeCredentialError) as refused:
            self.ra._enforce_harness_credentials(self._agent(backend="claude-code"), {})
        self.assertIn("user-repo lane", str(refused.exception))
        self.assertIn("re-baseline", str(refused.exception))

    def test_starter_re_baselines_with_a_non_subscription_config(self):
        from nebuko.engine.lib.workflow_state.state_codec import WorkflowStateCodec

        self._seed_user_repo_state("alice")
        self._human("alice")
        agent = self._agent(backend="openhands", model_path="mistral/mistral-large-4", token="MISTRAL_API_KEY")
        self.ra._enforce_harness_credentials(agent, {})
        state = WorkflowStateCodec().load_prev().state
        self.assertEqual(state.lane, "shared")
        self.assertIsNone(state.credential_owner)
        self.assertEqual(state.lane_changed_from, "user-repo")
        self.assertEqual(state.lane_changed_by, "alice")

    def test_non_owner_cannot_re_baseline(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError

        self._seed_user_repo_state("alice")
        self._human("bob")
        agent = self._agent(backend="openhands", model_path="mistral/mistral-large-4", token="MISTRAL_API_KEY")
        with self.assertRaises(ClaudeCredentialError) as refused:
            self.ra._enforce_harness_credentials(agent, {})
        self.assertIn("rebaseline-owner", str(refused.exception))

    def test_bot_cannot_re_baseline(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError

        self._seed_user_repo_state("alice")
        os.environ["TRIGGER_AUTHOR_LOGIN"] = "nebuko[bot]"
        os.environ["TRIGGER_AUTHOR_TYPE"] = "Bot"
        os.environ["TRIGGER_COMMENT_HEADER"] = "<!-- workflow-continue -->"
        agent = self._agent(backend="openhands", model_path="mistral/mistral-large-4", token="MISTRAL_API_KEY")
        with self.assertRaises(ClaudeCredentialError) as refused:
            self.ra._enforce_harness_credentials(agent, {})
        self.assertIn("lane-binding", str(refused.exception))

    def test_fresh_run_on_a_user_repo_state_is_not_refused(self):
        self._seed_user_repo_state("alice")
        self._human("bob", header="/nebuko run workflow --name dev-review-loop")
        self.ra._enforce_harness_credentials(self._agent(backend="claude-code"), {})

    def test_other_human_step_resume_cannot_move_the_payer(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError

        self._seed_user_repo_state("alice")
        self._human("bob", header="/nebuko run workflow --step plan --config agent-config-mistral.yaml")
        agent = self._agent(backend="openhands", model_path="mistral/mistral-large-4", token="MISTRAL_API_KEY")
        with self.assertRaises(ClaudeCredentialError) as refused:
            self.ra._enforce_harness_credentials(agent, {})
        self.assertIn("lane-binding", str(refused.exception))

    def test_starter_step_resume_re_baselines_the_lane(self):
        from nebuko.engine.lib.workflow_state.state_codec import WorkflowStateCodec

        self._seed_user_repo_state("alice")
        self._human("alice", header="/nebuko run workflow --step plan --config agent-config-mistral.yaml")
        agent = self._agent(backend="openhands", model_path="mistral/mistral-large-4", token="MISTRAL_API_KEY")
        self.ra._enforce_harness_credentials(agent, {})
        state = WorkflowStateCodec().load_prev().state
        self.assertEqual(state.lane, "shared")
        self.assertEqual(state.lane_changed_by, "alice")

    def test_starter_step_resume_on_a_subscription_config_is_refused(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError

        self._seed_user_repo_state("alice")
        self._human("alice", header="/nebuko run workflow --step plan")
        with self.assertRaises(ClaudeCredentialError) as refused:
            self.ra._enforce_harness_credentials(self._agent(backend="claude-code"), {})
        self.assertIn("rebaseline-config", str(refused.exception))

    def test_untrusted_only_thread_fails_closed(self):
        from nebuko.engine.harness.credentials import ClaudeCredentialError
        from nebuko.engine.lib.workflow_state.state_codec import WorkflowStateCodec

        WorkflowStateCodec().mark_untrusted_handoff(True)
        self._human("bob")
        with self.assertRaises(ClaudeCredentialError) as refused:
            self.ra._enforce_harness_credentials(self._agent(backend="claude-code"), {})
        message = str(refused.exception)
        self.assertIn("user-repo lane", message)
        self.assertIn("not a lane violation", message)
        self.assertIn("nebuko[bot]", message)
        self.assertIn("/nebuko run workflow", message)

    def test_untrusted_only_thread_is_not_refused_on_a_fresh_run(self):
        from nebuko.engine.lib.workflow_state.state_codec import WorkflowStateCodec

        WorkflowStateCodec().mark_untrusted_handoff(True)
        self._human("bob", header="/nebuko run workflow --name dev-review-loop")
        self.ra._enforce_harness_credentials(self._agent(backend="claude-code"), {})

    def test_newer_schema_lane_state_fails_closed(self):
        import json

        from nebuko.engine.harness.credentials import ClaudeCredentialError
        from nebuko.engine.lib.workflow_state.state_codec import (
            HANDOFF_FILENAME,
            STATE_SCHEMA_VERSION,
            WorkflowStateCodec,
        )

        self._seed_user_repo_state("alice")
        path = os.path.join(self._tmp.name, HANDOFF_FILENAME)
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        raw["schema_version"] = STATE_SCHEMA_VERSION + 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        self.assertTrue(WorkflowStateCodec().load_prev().refused_newer_lane)
        self._human("bob")
        with self.assertRaises(ClaudeCredentialError) as refused:
            self.ra._enforce_harness_credentials(self._agent(backend="claude-code"), {})
        self.assertIn("user-repo lane", str(refused.exception))

    def test_corrupt_handoff_does_not_read_as_a_user_repo_lane(self):
        from nebuko.engine.lib.workflow_state.state_codec import HANDOFF_FILENAME

        with open(os.path.join(self._tmp.name, HANDOFF_FILENAME), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self._human("bob")
        self.ra._enforce_harness_credentials(self._agent(backend="claude-code"), {})

    def test_repository_dispatch_event_file_binds_the_user_repo_lane(self):
        import json

        from nebuko.engine.lib.workflow_state.workflow_state import (
            dispatch_actor_from_event,
            lane_binding_from_env,
        )

        event = os.path.join(self._tmp.name, "event.json")
        with open(event, "w", encoding="utf-8") as handle:
            json.dump({"client_payload": {"actor": "alice"}}, handle)
        os.environ.update(
            GITHUB_EVENT_NAME="repository_dispatch",
            GITHUB_EVENT_PATH=event,
            GITHUB_REPOSITORY_OWNER="alice",
        )
        actor = dispatch_actor_from_event(os.environ)
        self.assertEqual(lane_binding_from_env(os.environ, actor), ("user-repo", "alice"))
        os.environ["GITHUB_REPOSITORY_OWNER"] = "nebuko"
        self.assertEqual(lane_binding_from_env(os.environ, actor), (None, None))

    def _bot_dispatch_env(self, payload_actor):
        import json

        event = os.path.join(self._tmp.name, "event.json")
        with open(event, "w", encoding="utf-8") as handle:
            json.dump({"client_payload": {"actor": payload_actor}}, handle)
        os.environ.update(
            GITHUB_EVENT_NAME="repository_dispatch",
            GITHUB_EVENT_PATH=event,
            GITHUB_ACTOR="nebuko-staging[bot]",
            GITHUB_REPOSITORY="alice/nebuko-personal",
            GITHUB_REPOSITORY_OWNER="alice",
        )
        for key in ("TRIGGER_AUTHOR_LOGIN", "TRIGGER_AUTHOR_TYPE"):
            os.environ.pop(key, None)

    def test_bot_created_dispatch_with_owner_payload_binds_the_user_repo_lane(self):
        from nebuko.engine.harness.credentials import authorize_lane_binding

        self._bot_dispatch_env("alice")
        ctx = self.ra._trigger_facts_from_env()
        self.assertEqual((ctx.trigger_login, ctx.trigger_type), ("", ""))
        self.assertIsNone(self.ra._continuation_from_env())
        self.assertEqual((ctx.lane, ctx.credential_owner), ("user-repo", "alice"))
        self.assertTrue(authorize_lane_binding(ctx).allowed)

    def test_bot_created_dispatch_with_other_payload_actor_is_not_the_user_lane(self):
        self._bot_dispatch_env("mallory")
        ctx = self.ra._trigger_facts_from_env()
        self.assertEqual((ctx.lane, ctx.credential_owner), (None, None))
