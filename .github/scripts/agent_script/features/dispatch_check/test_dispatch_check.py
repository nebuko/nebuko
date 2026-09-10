"""Tests for the shared dispatch-decision predicate (issue #524).

Round-2 blocking item: the recall gate must treat a parseable legacy
sentinel as a decision — not just a recorded dispatch_next call — so that
sentinel-only legacy runs never trigger needless recall turns.
"""

import unittest
from unittest import mock

from agent_script.features.dispatch_check import (
    agent_to_function_call,
    has_dispatch_decision,
)
from agent_script.features.dispatch_next_tool import record_dispatch, reset_registry
from workflow_state import Dispatch


VALID_SENTINEL = (
    '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", '
    '"task": "dev"}} -->'
)


class _FakeConv:
    def __init__(self, conv_id):
        self.id = conv_id


def _fresh_run_id(prefix: str) -> str:
    """Unique registry key, cleaned up around each test."""
    reset_registry()
    return prefix


class TestHasDispatchDecision(unittest.TestCase):
    """The predicate mirrors _resolve_dispatch's two sources."""

    def test_tool_call_counts(self):
        run_id = _fresh_run_id("run-tool")
        record_dispatch(run_id, Dispatch(status="advance"))
        self.assertTrue(has_dispatch_decision(_FakeConv(run_id), None))

    def test_parseable_sentinel_counts_without_tool_call(self):
        """A legacy sentinel-only run is a decision: no recall fires."""
        run_id = _fresh_run_id("run-sentinel")
        self.assertTrue(has_dispatch_decision(_FakeConv(run_id), VALID_SENTINEL))

    def test_no_source_is_no_decision(self):
        run_id = _fresh_run_id("run-none")
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), "just text"))
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), None))

    def test_unparseable_sentinel_does_not_count(self):
        """A broken sentinel is NOT a decision (it fails in
        _resolve_dispatch too) — those runs still get recalled."""
        run_id = _fresh_run_id("run-bad")
        broken = "<!-- AGENT_RUN_CONFIG {not json"
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), broken))

    def test_invalid_sentinel_payload_does_not_count(self):
        """A sentinel that parses but maps to an invalid Dispatch does not
        count — same rule as the resolver (issue #512 class)."""
        run_id = _fresh_run_id("run-invalid")
        invalid = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "bogus"}} -->'
        self.assertFalse(has_dispatch_decision(_FakeConv(run_id), invalid))


class TestAgentToFunctionCall(unittest.TestCase):
    """The shared call_agent spec builder (warn-only on unknown names)."""

    def test_known_agent(self):
        # Genius is registered in agent-config.yaml, so this also proves the
        # roster lookup works in the test environment (non-empty roster).
        self.assertEqual(
            agent_to_function_call("genius"),
            {"function": "call_agent", "agent_name": "genius"},
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