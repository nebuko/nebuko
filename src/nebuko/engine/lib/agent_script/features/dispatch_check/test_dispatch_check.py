"""Tests for the shared turn-outcome predicate (issues #524, #964).

Round-2 blocking item recast onto the factual design: the recall gate must
treat *either* structured channel (a recorded ``dispatch_next`` outcome or a
typed ``call_agent`` hand-off) as a decision, so decision-bearing runs never
trigger needless recall turns. The ``AGENT_RUN_CONFIG`` prose channel is gone
(issue #964), so a message carrying only a stale echo is not a decision.
"""

import unittest

from nebuko.engine.lib.agent_script.features.dispatch_check import (
    agent_to_function_call,
    has_dispatch_decision,
)
from nebuko.engine.lib.agent_script.features.dispatch_next_tool import (
    record_turn_outcome,
    reset_registry,
)
from nebuko.engine.lib.agent_script.features.call_agent_tool import (
    record_function_call,
    reset_registry as reset_function_call_registry,
)
from nebuko.engine.lib.workflow_state import FunctionCall, TurnOutcome


# A stale echo of the deleted sentinel: prose, never a routing input (#964).
STALE_SENTINEL = (
    '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", '
    '"task": "dev"}} -->'
)


class _FakeConv:
    def __init__(self, conv_id):
        self.id = conv_id


def _fresh_run_id(prefix: str) -> str:
    """Unique registry key, cleaned up around each test."""
    reset_registry()
    reset_function_call_registry()
    return prefix


class TestHasDispatchDecision(unittest.TestCase):
    """The predicate mirrors _resolve_outcome's two structured sources."""

    def test_tool_call_counts(self):
        run_id = _fresh_run_id("run-tool")
        record_turn_outcome(
            run_id, TurnOutcome(status="completed", appreciation="approved")
        )
        self.assertTrue(has_dispatch_decision(_FakeConv(run_id), None))

    def test_stale_sentinel_echo_alone_is_not_a_decision(self):
        """A sentinel-only message is no longer a decision (issue #964): the
        run states no verdict, so the recall pass must fire."""
        run_id = _fresh_run_id("run-sentinel")
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), STALE_SENTINEL))

    def test_no_source_is_no_decision(self):
        run_id = _fresh_run_id("run-none")
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), "just text"))
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), None))

    def test_a_malformed_sentinel_echo_is_not_a_decision(self):
        run_id = _fresh_run_id("run-bad")
        broken = "<!-- AGENT_RUN_CONFIG {not json"
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), broken))

    def test_an_invalid_sentinel_echo_is_not_a_decision(self):
        run_id = _fresh_run_id("run-invalid")
        invalid = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "bogus"}} -->'
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), invalid))

    def test_typed_call_agent_handoff_suppresses_recall(self):
        """A recorded typed ``call_agent`` is itself a decision (issue #876),
        so the recall pass must not fire for a hand-off-only run — exactly as
        for a ``dispatch_next`` call, and without any sentinel in the message.
        """
        run_id = _fresh_run_id("run-call-agent")
        record_function_call(
            run_id, FunctionCall(function="call_agent", agent_name="context")
        )
        self.assertTrue(has_dispatch_decision(_FakeConv(run_id), None))


class TestAgentToFunctionCall(unittest.TestCase):
    """The shared call_agent spec builder (warn-only on unknown names)."""

    def test_known_agent(self):
        # Architect is registered in agent-config.yaml, so this also proves the
        # roster lookup works in the test environment (non-empty roster).
        self.assertEqual(
            agent_to_function_call("architect"),
            {"function": "call_agent", "agent_name": "architect"},
        )

    def test_unknown_agent_warns_but_passes_through(self):
        """A typo'd name is logged, not dropped — downstream routing rejects
        it loudly instead of the target being lost silently."""
        spec = agent_to_function_call("totally-not-an-agent")
        self.assertEqual(
            spec,
            {"function": "call_agent", "agent_name": "totally-not-an-agent"},
        )


if __name__ == "__main__":
    unittest.main()