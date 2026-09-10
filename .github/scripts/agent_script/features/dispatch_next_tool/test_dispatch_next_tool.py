"""Tests for the dispatch_next tool (issue #524)."""

import unittest

from agent_script.features.dispatch_next_tool import (
    DispatchNextAction,
    DispatchNextTool,
    action_to_dispatch,
    clear_run,
    get_recorded_dispatch,
    reset_registry,
)
from workflow_state import Dispatch


class TestDispatchNextActionToDispatch(unittest.TestCase):
    """Test the action -> Dispatch mapping."""

    def setUp(self):
        reset_registry()

    def test_valid_action_maps_to_dispatch(self):
        act = DispatchNextAction(
            status="advance", agent="ctx", complexity="low", reason="need context"
        )
        d = action_to_dispatch(act)
        self.assertIsInstance(d, Dispatch)
        self.assertEqual(d.status, "advance")
        self.assertEqual(d.task_hint, "ctx")
        self.assertEqual(d.complexity, "low")
        self.assertEqual(d.reason, "need context")

    def test_defaults_apply(self):
        act = DispatchNextAction(status="advance")
        d = action_to_dispatch(act)
        self.assertIsNotNone(d)
        self.assertEqual(d.status, "advance")
        self.assertIsNone(d.task_hint)
        self.assertEqual(d.complexity, "medium")
        self.assertIsNone(d.reason)

    def test_invalid_status_returns_none(self):
        act = DispatchNextAction(status="bogus")
        self.assertIsNone(action_to_dispatch(act))


class TestDispatchNextTool(unittest.TestCase):
    """Test the ToolDefinition + executor behaviour."""

    def setUp(self):
        reset_registry()

    def test_create_produces_registered_tool(self):
        tools = DispatchNextTool.create()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "dispatch_next")

    def test_executor_records_dispatch(self):
        tool = DispatchNextTool.create(conversation_id="run-1")[0]
        obs = tool.executor(
            DispatchNextAction(status="hold", agent=None, reason="waiting")
        )
        self.assertFalse(obs.is_error)
        d = get_recorded_dispatch("run-1")
        self.assertIsNotNone(d)
        self.assertEqual(d.status, "hold")
        self.assertEqual(d.task_hint, None)
        clear_run("run-1")
        self.assertIsNone(get_recorded_dispatch("run-1"))

    def test_executor_rejects_invalid_payload(self):
        tool = DispatchNextTool.create(conversation_id="run-2")[0]
        obs = tool.executor(DispatchNextAction(status="bogus"))
        self.assertTrue(obs.is_error)
        self.assertIsNone(get_recorded_dispatch("run-2"))

    def test_latest_dispatch_wins(self):
        tool = DispatchNextTool.create(conversation_id="run-3")[0]
        tool.executor(DispatchNextAction(status="advance", agent="dev"))
        tool.executor(DispatchNextAction(status="loop", agent=None))
        d = get_recorded_dispatch("run-3")
        self.assertIsNotNone(d)
        # Guard the dereference above: a missing dispatch would raise AttributeError.
        self.assertIsInstance(d, Dispatch)
        self.assertEqual(d.status, "loop")
        clear_run("run-3")

    def test_create_rejects_unknown_params(self):
        with self.assertRaises(ValueError):
            DispatchNextTool.create(foo=1)

    def test_create_binds_conversation_state_id(self):
        """SDK resolves tools with the ConversationState; its id (a UUID)
        is what must key the registry — not a non-existent attribute."""

        class _FakeState:
            id = "state-uuid-1"

        tool = DispatchNextTool.create(conv_state=_FakeState())[0]
        tool.executor(DispatchNextAction(status="advance"))
        d = get_recorded_dispatch("state-uuid-1")
        self.assertIsNotNone(d)
        # Must NOT have leaked into the default key.
        self.assertIsNone(get_recorded_dispatch("default"))
        clear_run("state-uuid-1")


if __name__ == "__main__":
    unittest.main()
