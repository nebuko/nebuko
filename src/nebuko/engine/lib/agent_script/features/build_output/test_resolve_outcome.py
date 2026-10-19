"""Tests for _resolve_outcome (issue #524 / #964) — outcome resolution priority.

The reviewer's round-2 blocking item recast onto the factual design: a run that
calls ``dispatch_next`` must route from that recorded tool call even when the
posted message carries a stale ``AGENT_RUN_CONFIG`` echo, and the stale echo is
never read as routing input (issue #964).

Issue #864 adds the callback-aware no-action fallback: a decision-less run with
a return address (``CONFIG_CALLBACK``) advances back to its caller instead of
holding.
"""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from build_output import _neutralize_self_and_pin, _resolve_outcome
from workflow_state import Appreciation, FunctionCall, RunPrefs, TurnOutcome, WorkflowState


class _FakeConv:
    def __init__(self, conv_id):
        self.id = conv_id


FIXTURE_864 = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "workflow_state_issue864_context_hold.json"
)


# A stale echo of the deleted sentinel: prose the agent rendered, never a
# routing input since #964.
STALE_SENTINEL = (
    '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", '
    '"task": "dev"}} -->'
)

# The concrete invocation of the deleted RFC 6902 JSON-patch helper.
STALE_PATCH = (
    '<!-- AGENT_RUN_CONFIG {"patch": [{"op": "add", '
    '"path": "/routing", "value": {}}]} -->'
)


def _resolved(conv_id: str, outcome=None, function_call=None, message="answer"):
    """Drive _resolve_outcome with both agent registries stubbed."""
    return mock.patch.multiple(
        "build_output",
        get_recorded_turn_outcome=mock.DEFAULT,
        get_recorded_function_call=mock.DEFAULT,
    )


class TestResolveOutcome(unittest.TestCase):
    """The recorded tool call is the resolution authority."""

    def _resolve(self, fake, outcome=None, function_call=None, prefs=None):
        with mock.patch(
            "build_output.get_recorded_turn_outcome", return_value=outcome
        ), mock.patch(
            "build_output.get_recorded_run_prefs", return_value=prefs
        ), mock.patch(
            "build_output.get_recorded_function_call", return_value=function_call
        ):
            return _resolve_outcome(fake)

    def test_recorded_outcome_wins_over_a_stale_sentinel_echo(self):
        """A recorded tool outcome routes even when the message still carries
        a different (stale) sentinel (round-2 required test 1)."""
        recorded = TurnOutcome(
            status="completed", appreciation="hold", hold_kind="awaiting_input"
        )
        resolved, _, function_call = self._resolve(
            _FakeConv("conv-stale"), outcome=recorded
        )
        self.assertEqual(resolved, recorded)
        self.assertIsNone(function_call)

    def test_a_recorded_outcome_carries_no_target_of_its_own(self):
        """The verdict names no target (issue #1033): the hand-off is a
        separate ``function_call``, and an outcome without one keeps the
        workflow-relative route."""
        recorded = TurnOutcome(status="completed", appreciation="approved")
        resolved, _, function_call = self._resolve(
            _FakeConv("conv-target"), outcome=recorded
        )
        self.assertEqual(resolved, recorded)
        self.assertIsNone(function_call)

    def test_prefs_complexity_rides_the_derived_hand_off_separately(self):
        """The declared tier is a non-routing run preference (issue #787): it
        rides out of resolution on RunPrefs and onto the derived hand-off,
        never on the outcome itself.

        This pins the mapper; the write lane (tool call -> registry ->
        resolution) is covered by ``TestAgentPrefsReachTheResolution`` in
        ``test_build_output``.
        """
        recorded = TurnOutcome(status="completed", appreciation="approved")
        call = FunctionCall(
            function="call_agent", agent_name="context", complexity="high"
        )
        _, prefs, function_call = self._resolve(
            _FakeConv("conv-fc-complexity"),
            outcome=recorded,
            function_call=call,
            prefs=RunPrefs(complexity="high"),
        )
        self.assertEqual(function_call.agent_name, "context")
        # The tier is not part of the routing fact at all.
        self.assertNotIn("complexity", recorded.model_dump())
        # ...but it is carried beside it, and onto the derived hand-off.
        self.assertEqual(prefs.complexity, "high")
        self.assertEqual(function_call.complexity, "high")

    def test_no_recorded_prefs_defaults_to_empty(self):
        """A recorded outcome with no prefs beside it resolves to an empty
        RunPrefs, so the resolver applies the step's declared tier (#787)."""
        recorded = TurnOutcome(status="completed", appreciation="approved")
        _, prefs, _ = self._resolve(_FakeConv("conv-no-prefs"), outcome=recorded)
        self.assertEqual(prefs, RunPrefs())
        self.assertFalse(prefs.pin)

    def test_no_verdict_anywhere_defaults_to_a_no_outcome_record(self):
        """Neither source yields a decision: record the absence of a verdict
        (issue #765/#864) instead of silently auto-advancing the workflow."""
        resolved, _, function_call = self._resolve(_FakeConv("conv-none"))
        self.assertEqual(resolved.status.value, "incomplete")
        self.assertEqual(resolved.incomplete_reason.value, "no_outcome")
        self.assertIsNone(function_call)

    def test_a_stale_unparsable_sentinel_echo_is_not_a_decision(self):
        """A present-but-unparsable sentinel echo is still a decision-less run
        (the echo is inert since #964)."""
        resolved, _, function_call = self._resolve(
            _FakeConv("conv-broken"),
        )
        self.assertEqual(resolved.incomplete_reason.value, "no_outcome")
        self.assertIsNone(function_call)

    def test_a_stale_patch_sentinel_echo_is_never_applied(self):
        """The deleted JSON-patch helper cannot be reached from prose (#964)."""
        resolved, _, function_call = self._resolve(_FakeConv("conv-patch"))
        self.assertEqual(resolved.status.value, "incomplete")
        self.assertIsNone(function_call)

    def test_recorded_outcome_without_a_target_keeps_function_call_none(self):
        """A workflow-relative outcome (no explicit target) must not
        fabricate a function_call."""
        recorded = TurnOutcome(status="completed", appreciation="approved")
        resolved, _, function_call = self._resolve(
            _FakeConv("conv-noagent"), outcome=recorded
        )
        self.assertEqual(resolved, recorded)
        self.assertIsNone(function_call)


class TestNoActionOutcomeFallback(unittest.TestCase):
    """The callback-aware no-action fallback (#864)."""

    def test_no_verdict_records_an_engine_measured_absence(self):
        """A rudderless decision-less run records INCOMPLETE/NO_OUTCOME, which
        the router turns into a human ping (#765)."""
        from build_output import _no_action_outcome

        outcome = _no_action_outcome()
        self.assertEqual(outcome.status.value, "incomplete")
        self.assertEqual(outcome.incomplete_reason.value, "no_outcome")
        self.assertIsNone(outcome.appreciation)

    def test_absence_with_a_diagnostic_carries_it_in_the_note(self):
        from build_output import _no_action_outcome

        outcome = _no_action_outcome("could not be parsed")
        self.assertIn("could not be parsed", outcome.note)

    def test_a_callback_is_not_yet_a_routing_fact(self):
        """``CONFIG_CALLBACK`` is resolved by the router, not by the outcome:
        the recorded absence stays an absence either way (#864)."""
        from build_output import _no_action_outcome

        with mock.patch.dict(os.environ, {"CONFIG_CALLBACK": "architect"}):
            outcome = _no_action_outcome()
        self.assertEqual(outcome.status.value, "incomplete")


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

    def test_fixture_records_the_absence_of_a_verdict(self):
        state = self._load_state()
        self.assertEqual(state.step, "context")
        self.assertEqual(state.callback, "architect")
        self.assertEqual(state.turn_outcome.status.value, "incomplete")
        self.assertEqual(state.turn_outcome.incomplete_reason.value, "no_outcome")

    def test_replay_records_the_decision_less_run(self):
        """The run emitted no structured decision, so resolution records the
        absence; ``CONFIG_CALLBACK`` is what lets the router send it back."""
        fake = _FakeConv("conv-864")
        with mock.patch(
            "build_output.get_recorded_turn_outcome", return_value=None
        ), mock.patch(
            "build_output.get_recorded_function_call", return_value=None
        ), mock.patch.dict(os.environ, {"CONFIG_CALLBACK": "architect"}):
            outcome, _, function_call = _resolve_outcome(fake)

        self.assertEqual(outcome.status.value, "incomplete")
        self.assertEqual(outcome.incomplete_reason.value, "no_outcome")
        self.assertIsNone(function_call)

    def test_replay_without_callback_is_the_same_absence(self):
        """Same decision-less run, no return address: the recorded fact is
        identical - the routing difference lives in the router."""
        fake = _FakeConv("conv-864b")
        env = {k: v for k, v in os.environ.items() if k != "CONFIG_CALLBACK"}
        with mock.patch(
            "build_output.get_recorded_turn_outcome", return_value=None
        ), mock.patch(
            "build_output.get_recorded_function_call", return_value=None
        ), mock.patch.dict(os.environ, env, clear=True):
            outcome, _, _ = _resolve_outcome(fake)

        self.assertEqual(outcome.status.value, "incomplete")
        self.assertEqual(outcome.incomplete_reason.value, "no_outcome")


class TestStaleSentinelEchoIsInert(unittest.TestCase):
    """A co-emitted ``AGENT_RUN_CONFIG`` echo no longer contributes routing.

    Before #964 the sentinel was a second routing channel whose fields were
    merged into the resolved dispatch. Routing is now engine truth: the echo
    carries no weight and is stripped only as stale-echo hygiene.
    """

    def _resolve(self, conv_id, outcome):
        with mock.patch(
            "build_output.get_recorded_turn_outcome", return_value=outcome
        ), mock.patch(
            "build_output.get_recorded_function_call", return_value=None
        ):
            return _resolve_outcome(_FakeConv(conv_id))

    def test_a_sentinel_verdict_cannot_override_the_recorded_one(self):
        recorded = TurnOutcome(status="completed", appreciation="approved")
        resolved, _, _ = self._resolve("conv-merge", recorded)
        self.assertIs(resolved.appreciation, Appreciation.APPROVED)

    def test_a_sentinel_target_cannot_fabricate_a_hand_off(self):
        recorded = TurnOutcome(status="completed", appreciation="approved")
        _, _, function_call = self._resolve("conv-merge-none", recorded)
        self.assertIsNone(function_call)


class TestTypedCallAgentResolution(unittest.TestCase):
    """The typed ``call_agent`` tool is a first-class routing decision (#876).

    Structured replacement for the sentinel-carried ``function_call``: without
    a ``dispatch_next`` sibling the typed hand-off *is* the turn's verdict.
    """

    def _resolve(self, fake, outcome, function_call, message="answer"):
        with mock.patch(
            "build_output.get_recorded_turn_outcome", return_value=outcome
        ), mock.patch(
            "build_output.get_recorded_function_call", return_value=function_call
        ):
            return _resolve_outcome(fake)

    def test_typed_call_agent_alone_is_the_decision(self):
        """No recorded outcome: the typed hand-off advances to the callee."""
        call = FunctionCall(
            function="call_agent", agent_name="context", complexity="high"
        )
        outcome, _, function_call = self._resolve(_FakeConv("conv-typed-only"), None, call)
        self.assertIs(outcome.appreciation, Appreciation.APPROVED)
        # The hand-off is recorded as the imperative channel alone; the
        # verdict carries no target of its own (issue #1033).
        self.assertEqual(function_call.agent_name, "context")

    def test_typed_call_agent_carries_its_declared_tier(self):
        """The hand-off's declared tier rides on RunPrefs (issue #787)."""
        call = FunctionCall(
            function="call_agent", agent_name="context", complexity="high"
        )
        _, prefs, _ = self._resolve(_FakeConv("conv-typed-tier"), None, call)
        self.assertEqual(prefs.complexity, "high")

    def test_typed_call_agent_beats_the_legacy_sentinel(self):
        """A typed hand-off wins over a co-emitted sentinel function_call."""
        call = FunctionCall(function="call_agent", agent_name="context")
        outcome, _, function_call = self._resolve(
            _FakeConv("conv-typed-beats-sentinel"), None, call
        )
        self.assertIs(outcome.appreciation, Appreciation.APPROVED)
        self.assertEqual(function_call.agent_name, "context")

    def test_recorded_outcome_and_typed_call_agent_coexist(self):
        """When both tools are called, the imperative hand-off target is kept."""
        recorded = TurnOutcome(status="completed", appreciation="approved")
        call = FunctionCall(function="call_agent", agent_name="context")
        outcome, _, function_call = self._resolve(
            _FakeConv("conv-typed-beats-dispatch"), recorded, call
        )
        self.assertEqual(outcome, recorded)
        self.assertEqual(function_call.agent_name, "context")


class TestSelfHandoffNeutralization(unittest.TestCase):
    """Self hand-off neutralization (issue #876 review, #618 defect 2a).

    A ``function_call`` targeting the running agent must not outrank the plain
    outcome / workflow-next routing, so the neutralizer drops it (issue
    #1033: the hand-off is the single routing channel and has no second copy
    on the outcome).
    """

    def test_self_handoff_drops_the_function_call(self):
        call = FunctionCall(function="call_agent", agent_name="dev")
        outcome = TurnOutcome(status="completed", appreciation="approved")
        outcome, prefs, function_call = _neutralize_self_and_pin(
            outcome, RunPrefs(), call, "dev"
        )
        self.assertIsNone(function_call)
        # The plain verdict survives so the router falls through to workflow-next.
        self.assertIs(outcome.appreciation, Appreciation.APPROVED)

    def test_self_handoff_preserves_a_different_agent_target(self):
        """A hand-off naming a *different* agent is not a self hand-off."""
        call = FunctionCall(function="call_agent", agent_name="context")
        outcome = TurnOutcome(status="completed", appreciation="approved")
        outcome, _, function_call = _neutralize_self_and_pin(
            outcome, RunPrefs(), call, "dev"
        )
        self.assertEqual(function_call.agent_name, "context")

    def test_a_self_handoff_is_dropped_whatever_its_complexity(self):
        """The hand-off is the only routing authority on the call: a self
        target is dropped outright (issue #1033)."""
        call = FunctionCall(
            function="call_agent", agent_name="dev", complexity="high"
        )
        outcome = TurnOutcome(status="completed", appreciation="approved")
        outcome, _, function_call = _neutralize_self_and_pin(
            outcome, RunPrefs(), call, "dev"
        )
        self.assertIsNone(function_call)

    def test_non_self_handoff_is_untouched(self):
        call = FunctionCall(function="call_agent", agent_name="review")
        outcome = TurnOutcome(status="completed", appreciation="approved")
        prefs = RunPrefs()
        new_outcome, new_prefs, function_call = _neutralize_self_and_pin(
            outcome, prefs, call, "dev"
        )
        self.assertIs(function_call, call)
        self.assertIs(new_outcome, outcome)
        self.assertIs(new_prefs, prefs)


class TestPoEscalationPin(unittest.TestCase):
    """A PO imperative hand-off forces the pin engine-side (issue #876 review).

    ``CallAgentAction`` exposes no ``pin`` field, so a prompt alone cannot pin
    the spec comment on a PO escalation. The engine forces ``prefs.pin``.
    """

    def test_po_handoff_forces_pin(self):
        call = FunctionCall(function="call_agent", agent_name="architect")
        outcome = TurnOutcome(status="completed", appreciation="approved")
        _, prefs, function_call = _neutralize_self_and_pin(
            outcome, RunPrefs(), call, "po"
        )
        self.assertTrue(prefs.pin)
        self.assertEqual(function_call.agent_name, "architect")

    def test_non_po_handoff_does_not_force_pin(self):
        call = FunctionCall(function="call_agent", agent_name="context")
        outcome = TurnOutcome(status="completed", appreciation="approved")
        _, prefs, _ = _neutralize_self_and_pin(outcome, RunPrefs(), call, "architect")
        self.assertFalse(prefs.pin)

    def test_po_without_a_handoff_does_not_force_pin(self):
        """Only the imperative hand-off path pins; a plain verdict is untouched."""
        outcome = TurnOutcome(status="completed", appreciation="approved")
        _, prefs, _ = _neutralize_self_and_pin(outcome, RunPrefs(), None, "po")
        self.assertFalse(prefs.pin)


if __name__ == "__main__":
    unittest.main()
