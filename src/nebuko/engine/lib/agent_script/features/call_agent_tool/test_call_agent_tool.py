"""Tests for the typed call_agent tool (issue #876).

Direct unit coverage for the module's own surface: the action -> FunctionCall
mapping (including the reject path), the run-scoped registry lifecycle
(record/latest-wins/reset), and the tool executor. Previously exercised only
indirectly via ``tests/engine/test_agent_host.py`` and
``build_output/test_resolve_dispatch.py``.
"""

import unittest

from nebuko.engine.lib.agent_script.features.call_agent_tool import (
    CALL_AGENT_DESCRIPTION,
    CallAgentAction,
    CallAgentTool,
    action_to_function_call,
    clear_run,
    get_recorded_function_call,
    record_function_call,
    reset_registry,
)
from nebuko.engine.lib.workflow_state import FunctionCall


class TestActionToFunctionCall(unittest.TestCase):
    """The action -> canonical FunctionCall mapping."""

    def test_maps_every_field(self):
        act = CallAgentAction(
            agent_name="context",
            task="collect context",
            callback="architect",
            complexity="high",
        )
        call = action_to_function_call(act)
        self.assertIsInstance(call, FunctionCall)
        self.assertEqual(call.function, "call_agent")
        self.assertEqual(call.agent_name, "context")
        self.assertEqual(call.complexity, "high")
        self.assertEqual(call.callback, "architect")

    def test_omitted_complexity_maps_to_empty(self):
        act = CallAgentAction(agent_name="dev")
        call = action_to_function_call(act)
        self.assertIsNotNone(call)
        self.assertEqual(call.agent_name, "dev")
        self.assertEqual(call.complexity, "")
        self.assertIsNone(call.callback)

    def test_rejects_an_empty_agent_name(self):
        """A missing target fails FunctionCall validation -> None, never a raise."""
        act = CallAgentAction(agent_name="")
        self.assertIsNone(action_to_function_call(act))


class TestRegistryLifecycle(unittest.TestCase):
    """The run-scoped function-call registry."""

    def setUp(self):
        reset_registry()

    def test_record_and_read_back(self):
        call = FunctionCall(function="call_agent", agent_name="context")
        record_function_call("run-1", call)
        self.assertIs(get_recorded_function_call("run-1"), call)

    def test_latest_hand_off_wins(self):
        record_function_call("run-2", FunctionCall(function="call_agent", agent_name="context"))
        record_function_call("run-2", FunctionCall(function="call_agent", agent_name="dev"))
        self.assertEqual(get_recorded_function_call("run-2").agent_name, "dev")

    def test_unknown_run_is_none(self):
        self.assertIsNone(get_recorded_function_call("nope"))

    def test_clear_run_drops_only_that_run(self):
        record_function_call("run-3", FunctionCall(function="call_agent", agent_name="dev"))
        record_function_call("run-4", FunctionCall(function="call_agent", agent_name="dev"))
        clear_run("run-3")
        self.assertIsNone(get_recorded_function_call("run-3"))
        self.assertIsNotNone(get_recorded_function_call("run-4"))

    def test_reset_clears_every_run(self):
        record_function_call("run-5", FunctionCall(function="call_agent", agent_name="dev"))
        reset_registry()
        self.assertIsNone(get_recorded_function_call("run-5"))


class TestCallAgentTool(unittest.TestCase):
    """ToolDefinition + executor behaviour."""

    def setUp(self):
        reset_registry()

    def test_create_produces_registered_tool(self):
        tools = CallAgentTool.create(conversation_id="run-6")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "call_agent")
        self.assertIn("call_agent", CALL_AGENT_DESCRIPTION)

    def test_executor_records_the_handoff(self):
        tool = CallAgentTool.create(conversation_id="run-7")[0]
        obs = tool.executor(CallAgentAction(agent_name="context", complexity="high"))
        self.assertFalse(obs.is_error)
        self.assertTrue(obs.recorded)
        recorded = get_recorded_function_call("run-7")
        self.assertEqual(recorded.agent_name, "context")
        self.assertEqual(recorded.complexity, "high")

    def test_executor_rejects_an_invalid_payload(self):
        tool = CallAgentTool.create(conversation_id="run-8")[0]
        obs = tool.executor(CallAgentAction(agent_name=""))
        self.assertTrue(obs.is_error)
        self.assertIsNone(get_recorded_function_call("run-8"))

    def test_create_rejects_unknown_params(self):
        with self.assertRaises(ValueError):
            CallAgentTool.create(foo=1)

    def test_create_binds_conversation_state_id(self):
        """The registry key is the ConversationState id, not a random attr."""

        class _FakeState:
            id = "state-uuid-876"

        tool = CallAgentTool.create(conv_state=_FakeState())[0]
        tool.executor(CallAgentAction(agent_name="dev"))
        self.assertIsNotNone(get_recorded_function_call("state-uuid-876"))
        self.assertIsNone(get_recorded_function_call("default"))


if __name__ == "__main__":
    unittest.main()
