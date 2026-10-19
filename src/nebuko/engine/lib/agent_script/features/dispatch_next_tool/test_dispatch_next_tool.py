"""Tests for the dispatch_next tool (issues #524, #964)."""

import unittest

from pydantic import ValidationError

from agent_script.features.dispatch_next_tool import (
    DispatchNextTool,
    TurnOutcomeAction,
    action_to_payload,
    action_to_prefs,
    clear_run,
    get_recorded_run_prefs,
    get_recorded_turn_outcome,
    reset_registry,
)
from workflow_state import TurnOutcome


class TestTurnOutcomeActionToPayload(unittest.TestCase):
    """Test the action -> agent-payload mapping."""

    def setUp(self):
        reset_registry()

    def test_valid_action_maps_to_payload(self):
        act = TurnOutcomeAction(
            appreciation="approved",
            required_agent="context",
            note="need context",
        )
        payload = action_to_payload(act)
        self.assertEqual(payload["appreciation"], "approved")
        self.assertEqual(payload["required_agent"], "context")
        self.assertEqual(payload["note"], "need context")

    def test_defaults_apply(self):
        act = TurnOutcomeAction(appreciation="approved")
        payload = action_to_payload(act)
        self.assertEqual(payload, {"appreciation": "approved"})
        prefs = action_to_prefs(act)
        # An unspecified complexity stays unset so the resolver can apply the
        # workflow step's declared tier (issue #787).
        self.assertEqual(prefs.complexity, "")
        self.assertFalse(prefs.pin)

    def test_round_round_trip(self):
        """round survives the action -> payload mapping (issue #618)."""
        act = TurnOutcomeAction(appreciation="rejected", round=2)
        payload = action_to_payload(act)
        self.assertEqual(payload["round"], 2)

    def test_round_omitted_when_absent(self):
        act = TurnOutcomeAction(appreciation="approved")
        self.assertNotIn("round", action_to_payload(act))

    def test_pin_round_trip(self):
        """pin survives the action -> prefs mapping (issue #617)."""
        act = TurnOutcomeAction(appreciation="approved", pin=True)
        self.assertTrue(action_to_prefs(act).pin)
        self.assertNotIn("pin", action_to_payload(act))


class TestTurnOutcomeActionValidators(unittest.TestCase):
    """The tool schema is the first gate on a usable verdict (issue #964).

    A blank appreciation would read downstream as "no verdict" and advance the
    step on the workflow's default edge, so it must never reach the payload.
    """

    def test_blank_appreciation_is_rejected(self):
        for blank in ("", "   "):
            with self.assertRaises(ValidationError):
                TurnOutcomeAction(appreciation=blank)

    def test_unknown_hold_kind_is_rejected(self):
        with self.assertRaises(ValidationError):
            TurnOutcomeAction(appreciation="hold", hold_kind="bogus")

    def test_known_hold_kinds_are_accepted(self):
        # ``awaiting_approval`` was removed in issue #1033: approval gates are
        # engine-owned and only ``awaiting_input`` (ask the human) is a hold.
        act = TurnOutcomeAction(appreciation="hold", hold_kind="awaiting_input")
        self.assertEqual(action_to_payload(act)["hold_kind"], "awaiting_input")

    def test_awaiting_approval_is_rejected(self):
        with self.assertRaises(ValidationError):
            TurnOutcomeAction(appreciation="hold", hold_kind="awaiting_approval")

    def test_hold_kind_may_be_omitted(self):
        act = TurnOutcomeAction(appreciation="approved")
        self.assertNotIn("hold_kind", action_to_payload(act))


class TestDispatchNextTool(unittest.TestCase):
    """Test the ToolDefinition + executor behaviour."""

    def setUp(self):
        reset_registry()

    def test_create_produces_registered_tool(self):
        tools = DispatchNextTool.create()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "dispatch_next")

    def test_executor_records_turn_outcome(self):
        tool = DispatchNextTool.create(conversation_id="run-1")[0]
        obs = tool.executor(
            TurnOutcomeAction(appreciation="hold", hold_kind="awaiting_input")
        )
        self.assertFalse(obs.is_error)
        outcome = get_recorded_turn_outcome("run-1")
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.appreciation.value, "hold")
        self.assertEqual(outcome.hold_kind.value, "awaiting_input")
        clear_run("run-1")
        self.assertIsNone(get_recorded_turn_outcome("run-1"))

    def test_executor_records_the_run_prefs(self):
        """The knobs travel with the outcome (issue #964 round 6).

        ``action_to_prefs`` had no production caller, so ``pin``/``complexity``
        were silently inert; the executor must record them alongside the
        outcome.
        """
        tool = DispatchNextTool.create(conversation_id="run-prefs")[0]
        tool.executor(
            TurnOutcomeAction(appreciation="approved", complexity="high", pin=True)
        )
        prefs = get_recorded_run_prefs("run-prefs")
        self.assertIsNotNone(prefs)
        self.assertEqual(prefs.complexity, "high")
        self.assertTrue(prefs.pin)
        clear_run("run-prefs")
        self.assertIsNone(get_recorded_run_prefs("run-prefs"))

    def test_executor_records_empty_prefs_when_none_supplied(self):
        tool = DispatchNextTool.create(conversation_id="run-no-prefs")[0]
        tool.executor(TurnOutcomeAction(appreciation="approved"))
        prefs = get_recorded_run_prefs("run-no-prefs")
        self.assertIsNotNone(prefs)
        self.assertEqual(prefs.complexity, "")
        self.assertFalse(prefs.pin)
        clear_run("run-no-prefs")

    def test_executor_rejects_invalid_payload(self):
        tool = DispatchNextTool.create(conversation_id="run-2")[0]
        obs = tool.executor(TurnOutcomeAction(appreciation="bogus"))
        self.assertTrue(obs.is_error)
        # The error must be actionable, not a silent drop (issue #864).
        self.assertIn("approved", obs.text)
        self.assertIsNone(get_recorded_turn_outcome("run-2"))

    def test_latest_outcome_wins(self):
        tool = DispatchNextTool.create(conversation_id="run-3")[0]
        tool.executor(TurnOutcomeAction(appreciation="approved"))
        tool.executor(
            TurnOutcomeAction(appreciation="hold", hold_kind="awaiting_input")
        )
        outcome = get_recorded_turn_outcome("run-3")
        self.assertIsNotNone(outcome)
        # Guard the dereference above: a missing outcome would raise AttributeError.
        self.assertIsInstance(outcome, TurnOutcome)
        self.assertEqual(outcome.appreciation.value, "hold")
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
        tool.executor(TurnOutcomeAction(appreciation="approved"))
        outcome = get_recorded_turn_outcome("state-uuid-1")
        self.assertIsNotNone(outcome)
        # Must NOT have leaked into the default key.
        self.assertIsNone(get_recorded_turn_outcome("default"))
        clear_run("state-uuid-1")

    def test_real_sdk_resolution_keys_registry_by_state_id(self):
        """End-to-end key consistency (round-2 non-blocking item 6).

        Exercises the REAL SDK path — ``resolve_tool(Tool("DispatchNextTool"),
        conv_state)`` — exactly as the agent loop does it. The registry is
        keyed on ``str(conv_state.id)`` at write time; the recall gate and
        ``_resolve_outcome`` read under ``str(conversation.id)``. Because
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
        tools[0].executor(
            TurnOutcomeAction(appreciation="approved", note="delivered")
        )

        # Read side keys under str(conversation.id) == str(state.id).
        retrieved = get_recorded_turn_outcome(str(state.id))
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.appreciation.value, "approved")
        self.assertEqual(retrieved.note, "delivered")
        clear_run(str(state.id))


if __name__ == "__main__":
    unittest.main()
