"""Tests for _resolve_dispatch (issue #524) — dispatch resolution priority.

The reviewer's round-2 blocking item: a run that calls dispatch_next must
route from the tool call even when the final message carries a stale or
different sentinel, and a sentinel-only legacy run must resolve identically
to today.
"""

import unittest
from unittest import mock

from build_output import _resolve_dispatch
from workflow_state import Dispatch, FunctionCall


class _FakeConv:
    def __init__(self, conv_id):
        self.id = conv_id


# Note: the sentinel payload uses the legacy "task" key (mapped to
# task_hint by agent_run_config_to_dispatch).
SENTINEL = (
    '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", '
    '"task": "dev"}} -->'
)


class TestResolveDispatch(unittest.TestCase):
    """Tool-call dispatch takes priority over the legacy sentinel."""

    def test_tool_dispatch_wins_over_stale_sentinel(self):
        """A recorded tool dispatch routes even when the message still
        carries a different (stale) sentinel (round-2 required test 1)."""
        fake = _FakeConv("conv-stale")
        recorded = Dispatch(status="hold", task_hint="review", reason="x")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=recorded
        ):
            dispatch, function_call, message = _resolve_dispatch(
                fake, SENTINEL
            )
        # Tool dispatch wins — NOT the stale sentinel's dev/advance.
        self.assertEqual(dispatch, recorded)
        self.assertEqual(function_call.agent_name, "review")
        # The sentinel is always stripped so it never leaks into the body.
        self.assertNotIn("AGENT_RUN_CONFIG", message)

    def test_sentinel_only_resolves_as_before(self):
        """No tool call: the legacy sentinel resolves exactly as today
        (round-2 required test 4, the dual-mode regression guard)."""
        fake = _FakeConv("conv-sentinel")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ):
            dispatch, function_call, message = _resolve_dispatch(
                fake, SENTINEL
            )
        self.assertIsInstance(dispatch, Dispatch)
        self.assertEqual(dispatch.status, "advance")
        self.assertEqual(dispatch.task_hint, "dev")
        self.assertIsNone(function_call)
        self.assertNotIn("AGENT_RUN_CONFIG", message)

    def test_no_dispatch_anywhere(self):
        """Neither source yields a decision: all three outputs stay empty."""
        fake = _FakeConv("conv-none")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ):
            dispatch, function_call, message = _resolve_dispatch(
                fake, "plain final answer"
            )
        self.assertIsNone(dispatch)
        self.assertIsNone(function_call)
        self.assertEqual(message, "plain final answer")

    def test_tool_dispatch_without_agent_keeps_function_call_none(self):
        """A workflow-relative dispatch (no explicit target) must not
        fabricate a function_call."""
        fake = _FakeConv("conv-noagent")
        recorded = Dispatch(status="advance")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=recorded
        ):
            dispatch, function_call, _ = _resolve_dispatch(fake, "answer")
        self.assertEqual(dispatch, recorded)
        self.assertIsNone(function_call)


if __name__ == "__main__":
    unittest.main()
