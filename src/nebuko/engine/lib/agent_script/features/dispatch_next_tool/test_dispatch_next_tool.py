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
            status="advance", agent="context", complexity="low", reason="need context"
        )
        d = action_to_dispatch(act)
        self.assertIsInstance(d, Dispatch)
        self.assertEqual(d.status, "advance")
        self.assertEqual(d.task_hint, "context")
        self.assertEqual(d.complexity, "low")
        self.assertEqual(d.reason, "need context")

    def test_defaults_apply(self):
        act = DispatchNextAction(status="advance")
        d = action_to_dispatch(act)
        self.assertIsNotNone(d)
        self.assertEqual(d.status, "advance")
        self.assertIsNone(d.task_hint)
        # An unspecified complexity stays unset so the resolver can apply the
        # workflow step's declared tier (issue #787).
        self.assertEqual(d.complexity, "")
        self.assertIsNone(d.reason)

    def test_branch_round_trip(self):
        """branch survives the action -> Dispatch mapping (issue #618)."""
        act = DispatchNextAction(status="advance", branch="approved")
        d = action_to_dispatch(act)
        self.assertIsInstance(d, Dispatch)
        self.assertEqual(d.branch, "approved")

    def test_branch_defaults_to_none(self):
        act = DispatchNextAction(status="advance")
        d = action_to_dispatch(act)
        self.assertIsNotNone(d)
        self.assertIsNone(d.branch)

    def test_pin_round_trip(self):
        """pin survives the action -> Dispatch mapping (issue #617)."""
        act = DispatchNextAction(status="advance", pin=True)
        d = action_to_dispatch(act)
        self.assertIsInstance(d, Dispatch)
        self.assertTrue(d.pin)

    def test_pin_defaults_to_false(self):
        act = DispatchNextAction(status="advance")
        d = action_to_dispatch(act)
        self.assertIsNotNone(d)
        self.assertFalse(d.pin)

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

    def test_real_sdk_resolution_keys_registry_by_state_id(self):
        """End-to-end key consistency (round-2 non-blocking item 6).

        Exercises the REAL SDK path — ``resolve_tool(Tool("DispatchNextTool"),
        conv_state)`` — exactly as the agent loop does it. The registry is
        keyed on ``str(conv_state.id)`` at write time; the recall gate and
        ``_resolve_dispatch`` read under ``str(conversation.id)``. Because
        ``Conversation.id`` returns ``state.id`` (verified in the SDK source),
        both sides use the same key. This test proves the write side: a tool
        resolved from a real (unvalidated) ConversationState records under a
        key retrievable via the same str() coercion.
        """
        import uuid

        from openhands.sdk.conversation.state import ConversationState
        from openhands.sdk.tool import Tool
        from openhands.sdk.tool.registry import register_tool, resolve_tool

        register_tool("DispatchNextTool", DispatchNextTool)
        state = ConversationState.model_construct(
            id=uuid.uuid4(), agent=None, workspace=None
        )
        tools = resolve_tool(Tool(name="DispatchNextTool"), state)
        tools[0].executor(DispatchNextAction(status="advance", agent="context"))

        # Read side keys under str(conversation.id) == str(state.id).
        retrieved = get_recorded_dispatch(str(state.id))
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.status, "advance")
        self.assertEqual(retrieved.task_hint, "context")
        clear_run(str(state.id))


if __name__ == "__main__":
    unittest.main()
