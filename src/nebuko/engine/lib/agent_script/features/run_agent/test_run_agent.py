"""Tests for the supervised run orchestration in run_agent (issue #489).

The Conversation is fully mocked; only the orchestration logic (watchdog
arming, arun invocation, wind-down trigger, outcome attachment) is exercised.
"""

import asyncio
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
from nebuko.engine.lib.agent_script.features.run_agent.run_failure import RunFailureKind
from nebuko.engine.lib.agent_script.features.run_agent.run_outcome import (
    OutcomeStatus,
    RunOutcome,
)
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
            run_agent(llm_config={}, prompt="do the thing")
        self.assertIs(convo.run_outcome.status, OutcomeStatus.FAILURE)
        self.assertIsNotNone(convo.run_outcome.failure)
        self.assertIs(
            convo.run_outcome.failure.kind, RunFailureKind.LLM_UNREACHABLE
        )

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


if __name__ == "__main__":
    unittest.main()
