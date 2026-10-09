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

    def test_tool_dispatch_complexity_propagates_to_function_call(self):
        """An explicit dispatch tier rides the derived call_agent hand-off
        (issue #787) so the callee keeps the caller's declared tier."""
        fake = _FakeConv("conv-fc-complexity")
        recorded = Dispatch(status="advance", task_hint="context", complexity="high")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=recorded
        ):
            _, function_call, _ = _resolve_dispatch(fake, "answer")
        self.assertEqual(function_call.agent_name, "context")
        self.assertEqual(function_call.complexity, "high")

    def test_tool_dispatch_unset_complexity_stays_unset_on_function_call(self):
        """No explicit tier -> the derived call keeps "" so the callee's step
        tier applies instead of being masked by a hardcoded default (#787)."""
        fake = _FakeConv("conv-fc-unset")
        recorded = Dispatch(status="advance", task_hint="context")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=recorded
        ):
            _, function_call, _ = _resolve_dispatch(fake, "answer")
        self.assertEqual(function_call.agent_name, "context")
        self.assertEqual(function_call.complexity, "")

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

    def test_no_dispatch_anywhere_defaults_to_hold(self):
        """Neither source yields a decision: default to the no-action hold
        (issue #765) instead of silently auto-advancing the workflow."""
        fake = _FakeConv("conv-none")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ):
            dispatch, function_call, message = _resolve_dispatch(
                fake, "plain final answer"
            )
        self.assertIsInstance(dispatch, Dispatch)
        self.assertEqual(dispatch.status, "hold")
        self.assertIn("no-action default", dispatch.reason)
        self.assertIsNone(function_call)
        self.assertEqual(message, "plain final answer")

    def test_unparsable_sentinel_defaults_to_hold_with_diagnostic(self):
        """A present-but-unparsable sentinel is still a decision-less run:
        hold, with the parse diagnostic carried in the observable reason."""
        fake = _FakeConv("conv-broken")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ):
            dispatch, function_call, _ = _resolve_dispatch(
                fake, '<!-- AGENT_RUN_CONFIG {"dispatch": {broken -->'
            )
        self.assertEqual(dispatch.status, "hold")
        self.assertIn("no-action default", dispatch.reason)
        self.assertIn("could not be parsed", dispatch.reason)
        self.assertIsNone(function_call)

    def test_explicit_dispatch_is_never_defaulted(self):
        """An explicit decision must pass through untouched."""
        fake = _FakeConv("conv-explicit")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ):
            dispatch, _, _ = _resolve_dispatch(fake, SENTINEL)
        self.assertEqual(dispatch.status, "advance")

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


class TestSentinelRoutingFieldMerge(unittest.TestCase):
    """A co-emitted sentinel fills branch/workflow/step left unset by the
    tool call (issue #618), without overriding tool values or inverting
    the #524 precedence."""

    def test_merge_sentinel_branch_into_tool_dispatch(self):
        recorded = Dispatch(status="advance", reason="routed")
        sentinel = (
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", '
            '"branch": "approved", "workflow": "plan-orchestration", '
            '"step": "plan"}} -->'
        )
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=recorded
        ):
            dispatch, function_call, message = _resolve_dispatch(
                _FakeConv("conv-merge"), sentinel
            )
        self.assertEqual(dispatch.status, "advance")  # tool owns status
        self.assertEqual(dispatch.branch, "approved")
        self.assertEqual(dispatch.workflow, "plan-orchestration")
        self.assertEqual(dispatch.step, "plan")
        self.assertIsNone(function_call)
        self.assertNotIn("AGENT_RUN_CONFIG", message)

    def test_tool_branch_not_overridden_by_sentinel(self):
        """A branch the tool explicitly set wins over the sentinel's."""
        recorded = Dispatch(status="advance", branch="rejected")
        sentinel = (
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", '
            '"branch": "approved"}} -->'
        )
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=recorded
        ):
            dispatch, _, _ = _resolve_dispatch(
                _FakeConv("conv-merge-guard"), sentinel
            )
        self.assertEqual(dispatch.branch, "rejected")

    def test_no_sentinel_leaves_tool_dispatch_unchanged(self):
        recorded = Dispatch(status="advance", reason="x")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=recorded
        ):
            dispatch, _, _ = _resolve_dispatch(
                _FakeConv("conv-merge-none"), "plain answer"
            )
        self.assertEqual(dispatch, recorded)
        self.assertIsNone(dispatch.branch)


if __name__ == "__main__":
    unittest.main()
