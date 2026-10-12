"""Tests for _resolve_dispatch (issue #524) — dispatch resolution priority.

The reviewer's round-2 blocking item: a run that calls dispatch_next must
route from the tool call even when the final message carries a stale or
different sentinel, and a sentinel-only legacy run must resolve identically
to today.

Issue #864 adds the callback-aware no-action fallback: a decision-less run
with a return address (`CONFIG_CALLBACK`) advances back to its caller instead
of holding.

Debug hook (replay the captured #864 state):

    python3 test_resolve_dispatch.py
"""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from build_output import _resolve_dispatch, _no_action_dispatch
from workflow_state import Dispatch, FunctionCall, WorkflowState


class _FakeConv:
    def __init__(self, conv_id):
        self.id = conv_id


FIXTURE_864 = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "workflow_state_issue864_context_hold.json"
)


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


class TestNoActionDispatchFallback(unittest.TestCase):
    """The callback-aware no-action fallback (#864)."""

    def test_no_callback_holds_with_engine_fallback_origin(self):
        """A rudderless decision-less run still holds for a human (#765)."""
        dispatch = _no_action_dispatch()
        self.assertEqual(dispatch.status, "hold")
        self.assertEqual(dispatch.origin, "engine_fallback")
        self.assertIn("no-action default", dispatch.reason)

    def test_callback_advances_to_caller(self):
        """A decision-less run with a return address advances back (#864)."""
        dispatch = _no_action_dispatch(callback="architect")
        self.assertEqual(dispatch.status, "advance")
        self.assertEqual(dispatch.origin, "engine_fallback")
        self.assertIn("architect", dispatch.reason)
        # No explicit destination: the callback gate must be free to fire.
        self.assertIsNone(dispatch.task_hint)
        self.assertIsNone(dispatch.workflow)
        self.assertIsNone(dispatch.step)

    def test_callback_defaults_to_agent_origin(self):
        """An agent-emitted dispatch is tagged 'agent', not a fallback."""
        self.assertEqual(Dispatch(status="advance").origin, "agent")


class TestIssue864FixtureReplay(unittest.TestCase):
    """Reproduce the issue's captured decision-less `context` run (#864).

    The fixture is the verbatim `workflow_state` from the issue: a `context`
    run that ended with the synthesized
    ``"no-action default: agent emitted no decision"`` hold while carrying
    ``callback: "architect"``.
    """

    def _load_state(self) -> WorkflowState:
        return WorkflowState.model_validate(
            json.loads(FIXTURE_864.read_text(encoding="utf-8"))
        )

    def test_fixture_is_the_reported_hold(self):
        state = self._load_state()
        self.assertEqual(state.step, "context")
        self.assertEqual(state.callback, "architect")
        self.assertEqual(state.dispatch.status, "hold")
        self.assertIn("no-action default", state.dispatch.reason)

    def test_replay_now_advances_back_to_architect(self):
        """With the fix, the same run returns to its caller instead of holding.

        The run emitted no structured decision (the tool payload was lost, the
        sentinel absent) so `_resolve_dispatch` synthesizes the fallback; with
        `CONFIG_CALLBACK=architect` it must advance back to the caller.
        """
        fake = _FakeConv("conv-864")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ), mock.patch.dict(os.environ, {"CONFIG_CALLBACK": "architect"}):
            dispatch, function_call, _ = _resolve_dispatch(fake, "report body")

        self.assertEqual(dispatch.status, "advance")
        self.assertEqual(dispatch.origin, "engine_fallback")
        self.assertIn("architect", dispatch.reason)
        self.assertIsNone(function_call)

    def test_replay_without_callback_stays_a_hold(self):
        """Same decision-less run, no return address -> unchanged #765 hold."""
        fake = _FakeConv("conv-864b")
        env = {k: v for k, v in os.environ.items() if k != "CONFIG_CALLBACK"}
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ), mock.patch.dict(os.environ, env, clear=True):
            dispatch, _, _ = _resolve_dispatch(fake, "report body")

        self.assertEqual(dispatch.status, "hold")
        self.assertEqual(dispatch.origin, "engine_fallback")


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


class TestTypedCallAgentResolution(unittest.TestCase):
    """The typed ``call_agent`` tool is a first-class routing decision (#876).

    Structured replacement for the sentinel-carried ``function_call``: a typed
    hand-off wins over the legacy sentinel and, without a ``dispatch_next``
    sibling, is itself the decision (advance to the named agent).
    """

    def _patch_call_agent(self, function_call):
        return mock.patch(
            "build_output.get_recorded_function_call", return_value=function_call
        )

    def test_typed_call_agent_alone_is_the_decision(self):
        """No tool dispatch, no sentinel: the typed hand-off advances."""
        fake = _FakeConv("conv-typed-only")
        call = FunctionCall(function="call_agent", agent_name="context", complexity="high")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ), self._patch_call_agent(call):
            dispatch, function_call, _ = _resolve_dispatch(fake, "answer")
        self.assertEqual(dispatch.status, "advance")
        self.assertEqual(dispatch.task_hint, "context")
        self.assertEqual(dispatch.complexity, "high")
        self.assertEqual(function_call.agent_name, "context")

    def test_typed_call_agent_beats_the_legacy_sentinel(self):
        """A typed hand-off wins over a co-emitted sentinel function_call."""
        fake = _FakeConv("conv-typed-beats-sentinel")
        call = FunctionCall(function="call_agent", agent_name="context")
        sentinel = (
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, '
            '"function_call": {"function": "call_agent", '
            '"agent_name": "review"}} -->'
        )
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ), self._patch_call_agent(call):
            dispatch, function_call, message = _resolve_dispatch(fake, sentinel)
        self.assertEqual(dispatch.task_hint, "context")
        self.assertEqual(function_call.agent_name, "context")
        self.assertNotIn("AGENT_RUN_CONFIG", message)

    def test_typed_call_agent_beats_dispatch_next_target(self):
        """When both tools are called, the imperative hand-off target wins."""
        fake = _FakeConv("conv-typed-beats-dispatch")
        recorded = Dispatch(status="advance", task_hint="review")
        call = FunctionCall(function="call_agent", agent_name="context")
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=recorded
        ), self._patch_call_agent(call):
            dispatch, function_call, _ = _resolve_dispatch(fake, "answer")
        self.assertEqual(dispatch.status, "advance")
        self.assertEqual(function_call.agent_name, "context")

    def test_sentinel_only_legacy_function_call_still_routes(self):
        """Absent a typed tool call the sentinel function_call still resolves."""
        fake = _FakeConv("conv-legacy-fc")
        sentinel = (
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, '
            '"function_call": {"function": "call_agent", '
            '"agent_name": "context", "callback": "architect"}} -->'
        )
        with mock.patch(
            "build_output.get_recorded_dispatch", return_value=None
        ), self._patch_call_agent(None):
            dispatch, function_call, message = _resolve_dispatch(fake, sentinel)
        self.assertEqual(dispatch.status, "advance")
        self.assertEqual(function_call.agent_name, "context")
        self.assertEqual(function_call.callback, "architect")
        self.assertNotIn("AGENT_RUN_CONFIG", message)


if __name__ == "__main__":
    unittest.main()
