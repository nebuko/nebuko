"""Root-level gate lock tests (issue #1003).

The reported bug: an agent parked on a ``human_gate`` step could not consult
another agent (``call_agent``) — the gate intercept owned the verdict *and* the
hand-off, so the delegation was destroyed and the re-trigger exited 20
(``TERMINAL``) instead of the deliberate hold exit 10.

The fix separates three concerns that were conflated: the turn **outcome**, the
**hand-off**, and the **gate lock**. The lock is now root-level metadata
(``WorkflowState.gate``), independent of ``position`` and of any step's
``turn_outcome``; it blocks the workflow *advance* only. These tests pin that
contract: a consult from an armed gate routes to the callee and back, the lock
survives, the forward edge is suppressed, the consult budget is bounded, and a
bare re-trigger on an armed gate is a benign ``HUMAN_GATE_HOLD`` hold (10).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path


from nebuko.engine.lib.config import StepRef  # noqa: E402

from nebuko.engine.lib.resolve_config import resolve_config  # noqa: E402
from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import (  # noqa: E402
    _armed_gate,
    _resolve_gate_consult,
    _resolve_gate_decision_path,
    _resolve_handoff_decision,
    _resolve_outcome_path,
    _resolve_resume_path,
)
from nebuko.engine.lib.resolve_config.sub_domains.author_identity.author_identity import (  # noqa: E402
    AuthorIdentity,
)
from nebuko.engine.lib.resolve_config.sub_domains.workflow_command.workflow_command import (  # noqa: E402
    parse_workflow_command,
)
from nebuko.engine.models import ExitCode, SkipKind, SkipOutcome  # noqa: E402
from nebuko.engine.lib.workflow_state import (  # noqa: E402
    Appreciation,
    FunctionCall,
    GateLock,
    GateStatus,
    HoldKind,
    MAX_CONSULT_ROUNDS,
    TurnOutcome,
    TurnStatus,
    WorkflowState,
)
from nebuko.engine.lib.workflow_state.features.build_state.build_state import build_state  # noqa: E402


# A hand-off the parse-time registry accepts but the routing guard rejects
# (there is no callee), i.e. a genuine config error rather than a benign hold.
UNSUPPORTED_CALL = FunctionCall.model_construct(
    function="call_agent", agent_name="", complexity="", reason=None, callback=None
)


GATED_WORKFLOW_YAML = (
    "name: default\n"
    "description: Standard workflow with a gated approval step\n"
    "trigger:\n  agents: [architect]\n"
    "steps:\n"
    "  - name: analyze\n"
    "    agent: architect\n"
    "    next: plan\n"
    "  - name: plan\n"
    "    agent: architect\n"
    "    human_gate: true\n"
    "    next: implement\n"
    "    conditions:\n"
    "      - on_result: approved\n"
    "        next: implement\n"
    "      - on_result: rejected\n"
    "        next: analyze\n"
    "  - name: implement\n"
    "    agent: dev\n"
    "    next: review\n"
    "  - name: review\n"
    "    agent: context\n"
    "    next: null\n"
)

HUMAN = AuthorIdentity(
    login="olivier", is_bot=False, association="MEMBER", is_machine_comment=False
)


def _gated_state(
    *,
    step: str = "plan",
    agent: str = "architect",
    gate: GateLock | None = None,
    function_call: FunctionCall | None = None,
    outcome: TurnOutcome | None = None,
    gate_history=None,
) -> WorkflowState:
    """A state parked on its ``human_gate`` with an armed root lock (#1003)."""
    return WorkflowState(
        workflow="default",
        step=step,
        agent=agent,
        turn_outcome=outcome
        or TurnOutcome(
            status=TurnStatus.COMPLETED,
            appreciation=Appreciation.APPROVED,
        ),
        position=[StepRef(workflow="default", step=step)],
        gate=gate if gate is not None else GateLock(step=step),
        function_call=function_call,
        gate_history=list(gate_history or []),
    )


def _write_workflow(tmpdir: str) -> str:
    with open(os.path.join(tmpdir, "default.yaml"), "w", encoding="utf-8") as f:
        f.write(GATED_WORKFLOW_YAML)
    return tmpdir


def _consult_call(agent: str = "context") -> FunctionCall:
    return FunctionCall(function="call_agent", agent_name=agent, callback="architect")


class TestGateLockModel(unittest.TestCase):
    """The ``GateLock`` entity: arming, consult budget, (de)serialization."""

    def test_default_lock_is_armed_with_full_budget(self):
        gate = GateLock(step="plan")
        self.assertTrue(gate.armed())
        self.assertTrue(gate.can_consult())
        self.assertEqual(gate.consult_rounds, 0)
        self.assertIsNone(gate.last_handoff)

    def test_released_lock_blocks_nothing(self):
        gate = GateLock(step="plan", status=GateStatus.RELEASED)
        self.assertFalse(gate.armed())
        self.assertFalse(gate.can_consult())

    def test_consult_budget_is_bounded(self):
        gate = GateLock(step="plan")
        for i in range(MAX_CONSULT_ROUNDS):
            self.assertTrue(gate.can_consult(), f"round {i} should be allowed")
            gate = gate.consume_consult("context")
        self.assertFalse(gate.can_consult())
        self.assertEqual(gate.consult_rounds, MAX_CONSULT_ROUNDS)
        self.assertEqual(gate.last_handoff, "context")

    def test_consume_consult_does_not_mutate_the_original(self):
        gate = GateLock(step="plan")
        spent = gate.consume_consult("context")
        self.assertEqual(gate.consult_rounds, 0)
        self.assertEqual(spent.consult_rounds, 1)

    def test_state_round_trip(self):
        gate = GateLock(step="plan", consult_rounds=2, last_handoff="context")
        restored = GateLock.from_state(gate.to_state())
        self.assertEqual(restored, gate)

    def test_legacy_blob_without_gate_loads_unlocked(self):
        self.assertIsNone(GateLock.from_state(None))
        self.assertIsNone(GateLock.from_state({}))

    def test_unknown_status_degrades_to_armed(self):
        """Fail-closed: a corrupt status must not silently release the gate."""
        restored = GateLock.from_state({"step": "plan", "status": "bogus"})
        self.assertTrue(restored.armed())

    def test_workflow_state_persists_the_gate_at_root(self):
        state = _gated_state()
        payload = state.model_dump(mode="json")
        self.assertEqual(payload["gate"]["step"], "plan")
        self.assertEqual(payload["gate"]["status"], "armed")
        # The lock lives at the root, never inside a step entry.
        for entry in payload.get("history", []):
            self.assertNotIn("gate", entry)

    def test_workflow_state_round_trips_a_released_gate(self):
        released = GateLock(step="plan", status=GateStatus.RELEASED)
        restored = WorkflowState.model_validate_json(
            _gated_state(gate=released).model_dump_json()
        )
        self.assertFalse(restored.gate.armed())


class TestArmedGateDetection(unittest.TestCase):
    """``_armed_gate`` is the single read of the root lock."""

    def test_armed_gate_detected(self):
        self.assertIsNotNone(_armed_gate(_gated_state()))

    def test_released_gate_is_not_armed(self):
        state = _gated_state(gate=GateLock(step="plan", status=GateStatus.RELEASED))
        self.assertIsNone(_armed_gate(state))

    def test_no_gate_and_no_state_are_safe(self):
        self.assertIsNone(_armed_gate(None))
        self.assertIsNone(_armed_gate(WorkflowState(workflow="default", step="plan")))


class TestGateConsultRouting(unittest.TestCase):
    """A ``call_agent`` from an armed gate is a consultation, not an advance."""

    def test_consult_routes_to_the_callee_with_the_gate_armed(self):
        state = _gated_state(function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_outcome_path(state, tmpdir)
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg["task"], "context")
        # The callback returns to the emitter at the *unchanged* gated step.
        self.assertEqual(cfg["config_callback"], "architect")
        self.assertEqual(cfg["config_workflow"], "default")
        self.assertEqual(cfg["config_step"], "review")

    def test_consult_keeps_the_position_on_the_gated_step(self):
        state = _gated_state(function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_outcome_path(state, tmpdir)
        self.assertEqual(cfg["config_step"], "review")
        # The position of the *caller* is untouched: the consult returns to
        # the gated step, and the root gate is still armed.
        self.assertEqual(state.step, "plan")
        self.assertEqual(cfg["config_workflow"], "default")
        self.assertTrue(state.gate.armed())

    def test_handoff_decision_lets_a_consult_through(self):
        state = _gated_state(function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            decision = _resolve_handoff_decision(state, tmpdir)
        # The gate never hides the callee: the hand-off still names 'context'
        # (issue #1003) and only its routing kind marks it as a consultation.
        self.assertEqual(decision.target_agent, "context")
        self.assertEqual(decision.reason_code, "gate_hold")

    def test_released_gate_routes_the_handoff_as_a_forward_handoff(self):
        """A released gate leaves run state unarmed, so the call is a normal
        hand-off rather than a consultation (issue #1033: the gate, not the
        agent's verdict, is what holds routing)."""
        state = _gated_state(
            gate=GateLock(step="plan", status=GateStatus.RELEASED),
            function_call=_consult_call("context"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            decision = _resolve_handoff_decision(state, tmpdir)
        self.assertEqual(decision.target_agent, "context")
        self.assertNotEqual(decision.reason_code, "gate_hold")

    def test_no_handoff_on_an_armed_gate_is_a_benign_hold(self):
        state = _gated_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_outcome_path(state, tmpdir)
        self.assertEqual(cfg["SKIP_AGENT"], "true")
        self.assertEqual(cfg["SKIP_KIND"], "human_gate_hold")
        self.assertIn("approve gate", cfg["REASON"])

    def test_forward_edge_is_suppressed_while_armed(self):
        """The gate blocks ``plan -> implement``: the run holds, never advances."""
        state = _gated_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_outcome_path(state, tmpdir)
        self.assertNotEqual(cfg.get("task"), "dev")

    def test_consult_budget_exhaustion_holds_with_a_note(self):
        spent = GateLock(
            step="plan", consult_rounds=MAX_CONSULT_ROUNDS, last_handoff="context"
        )
        state = _gated_state(gate=spent, function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_outcome_path(state, tmpdir)
        self.assertEqual(cfg["SKIP_AGENT"], "true")
        self.assertEqual(cfg["SKIP_KIND"], "human_gate_hold")
        self.assertIn("consult budget", cfg["REASON"])

    def test_malformed_handoff_stays_a_config_error(self):
        """A hand-off with no callee is a real error, not a benign gate hold."""
        state = _gated_state()
        # The model forbids an empty callee, so inject it post-validation: the
        # point is that a *corrupt* hand-off must not be masked as a benign hold.
        state.function_call = UNSUPPORTED_CALL
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_consult(state, state.gate, tmpdir)
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertNotEqual(cfg.get("SKIP_KIND"), "human_gate_hold")

    def test_consult_helper_returns_none_without_a_handoff(self):
        state = _gated_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            self.assertIsNone(_resolve_gate_consult(state, state.gate, tmpdir))

    def test_armed_gate_holds_even_without_a_turn_outcome(self):
        """The lock is root metadata: no outcome must still block the advance.

        A resolution-only state edge can carry an armed lock with no
        ``turn_outcome``; without the outcome-independent guard the router would
        fall through to the prefs path and advance past the gate.
        """
        state = _gated_state()
        state.turn_outcome = None
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_outcome_path(state, tmpdir)
        self.assertEqual(cfg["SKIP_AGENT"], "true")
        self.assertEqual(cfg["SKIP_KIND"], "human_gate_hold")
        self.assertNotEqual(cfg.get("task"), "dev")

    def test_armed_gate_holds_a_verdict_bearing_completion(self):
        """A completing verdict must not advance past an armed gate.

        The gate-hold branch keys off the *hold* shape, so a verdict-bearing
        completion (a re-entered step that finished with its own verdict) would
        otherwise fall through to the forward edge; the outcome-independent
        guard keeps the advance blocked.
        """
        state = _gated_state(
            outcome=TurnOutcome(
                status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_outcome_path(state, tmpdir)
        self.assertEqual(cfg["SKIP_AGENT"], "true")
        self.assertEqual(cfg["SKIP_KIND"], "human_gate_hold")


class TestGateConsultEndToEnd(unittest.TestCase):
    """Full consult round-trip through ``resolve_config`` (issue #1003).

    The helper-level cases above prove the routing *policy*; these drive the
    same path from a persisted workflow-state body the way production does, and
    prove the consult budget actually survives a transition: the resolver
    routes to the callee, ``build_output`` re-arms the lock, and the round
    counter (``gate.consult_rounds``) is persisted on the emitted state.
    """

    def _dirs(self, tmpdir: str) -> tuple[str, str]:
        prompt_base = os.path.join(tmpdir, "prompts")
        workflows_dir = os.path.join(tmpdir, "workflows")
        os.makedirs(prompt_base, exist_ok=True)
        os.makedirs(workflows_dir, exist_ok=True)
        _write_workflow(workflows_dir)
        return prompt_base, workflows_dir

    def _state_body(self, state: WorkflowState) -> str:
        block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + state.model_dump_json()
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        return "<!-- bot-workflow-terminal -->\n" + block

    def test_consult_routes_to_the_callee_through_resolve_config(self):
        state = _gated_state(function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_base, workflows_dir = self._dirs(tmpdir)
            cfg = resolve_config(
                self._state_body(state), prompt_base, workflows_dir, is_bot=True
            )
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg["TASK"], "context")
        # The callback returns to the emitter, whose position is untouched.
        self.assertEqual(cfg["CONFIG_CALLBACK"], "architect")

    def test_round_trip_marks_the_consult_for_the_callee_run(self):
        """Resolver → build_result: the consult resolution carries the callee.

        The marker is what production ``build_output`` consumes to spend the
        round (see ``test_build_output.TestGateLockArming``). Asserting it here
        pins the dispatcher half of the round-trip: a routed consult must
        advertise its callee, otherwise the lock never advances its counter.
        """
        state = _gated_state(function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_base, workflows_dir = self._dirs(tmpdir)
            cfg = resolve_config(
                self._state_body(state), prompt_base, workflows_dir, is_bot=True
            )
            self.assertEqual(cfg["TASK"], "context")
            # ``resolve_config`` returns the built result: the consult marker
            # must survive as the ``GATE_CONSULT`` env the callee run reads.
            self.assertEqual(cfg["GATE_CONSULT"], "context")

    def test_bare_retrigger_on_an_armed_gate_holds_with_exit_10(self):
        """Regression for the reported exit 20: a re-trigger must hold, not fail."""
        state = _gated_state(function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_base, workflows_dir = self._dirs(tmpdir)
            cfg = resolve_config(
                self._state_body(state), prompt_base, workflows_dir, is_bot=True
            )
            # The consult was routed, so the callee's run clears the hand-off;
            # the next bare trigger on the same armed gate must be a benign hold.
            state.function_call = None
            cfg = resolve_config(
                self._state_body(state), prompt_base, workflows_dir, is_bot=True
            )
        self.assertEqual(cfg["SKIP_AGENT"], "true")
        outcome = SkipOutcome.from_verdict(cfg)
        self.assertIs(outcome.kind, SkipKind.HUMAN_GATE_HOLD)
        self.assertTrue(outcome.is_benign)
        self.assertIs(outcome.exit_code, ExitCode.HOLD)

    def test_exhausted_budget_holds_and_names_the_spent_rounds(self):
        spent = GateLock(
            step="plan", consult_rounds=MAX_CONSULT_ROUNDS, last_handoff="context"
        )
        state = _gated_state(gate=spent, function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_base, workflows_dir = self._dirs(tmpdir)
            cfg = resolve_config(
                self._state_body(state), prompt_base, workflows_dir, is_bot=True
            )
        self.assertEqual(cfg["SKIP_AGENT"], "true")
        self.assertEqual(cfg["SKIP_KIND"], "human_gate_hold")
        self.assertIn("consult budget", cfg["REASON"])
        self.assertIs(SkipOutcome.from_verdict(cfg).exit_code, ExitCode.HOLD)

    def test_forward_edge_stays_suppressed_while_armed(self):
        """``plan -> implement`` must never fire on an armed gate."""
        state = _gated_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_base, workflows_dir = self._dirs(tmpdir)
            cfg = resolve_config(
                self._state_body(state), prompt_base, workflows_dir, is_bot=True
            )
        self.assertEqual(cfg["SKIP_AGENT"], "true")
        self.assertNotEqual(cfg.get("TASK"), "dev")


class TestHumanGateHoldVerdict(unittest.TestCase):
    """The benign skip kind that fixes the reported exit 20 (issue #1003)."""

    def test_human_gate_hold_is_benign_and_exits_hold(self):
        skip = SkipOutcome.from_verdict(
            {
                "SKIP_AGENT": "true",
                "SKIP_KIND": "human_gate_hold",
                "REASON": "human_gate:plan held",
            }
        )
        self.assertIs(skip.kind, SkipKind.HUMAN_GATE_HOLD)
        self.assertTrue(skip.is_benign)
        self.assertEqual(skip.exit_code, ExitCode.HOLD)
        self.assertNotEqual(skip.exit_code, ExitCode.TERMINAL)

    def test_unclassified_skip_still_fails_closed(self):
        """No ``SKIP_KIND`` must keep the pre-existing terminal failure."""
        skip = SkipOutcome.from_verdict({"SKIP_AGENT": "true", "REASON": "boom"})
        self.assertIs(skip.kind, SkipKind.CONFIG_ERROR)
        self.assertFalse(skip.is_benign)
        self.assertEqual(skip.exit_code, ExitCode.TERMINAL)


class TestGateNoopVerdict(unittest.TestCase):
    """The inert gate-command skip kind (issue #1012)."""

    def test_gate_noop_is_benign_and_exits_advance(self):
        skip = SkipOutcome.from_verdict(
            {
                "SKIP_AGENT": "true",
                "SKIP_KIND": "gate_noop",
                "REASON": "gate: workflow is not held at a human_gate step",
            }
        )
        self.assertIs(skip.kind, SkipKind.GATE_NOOP)
        self.assertTrue(skip.is_benign)
        self.assertEqual(skip.exit_code, ExitCode.ADVANCE)
        self.assertNotEqual(skip.exit_code, ExitCode.TERMINAL)
        self.assertNotEqual(skip.exit_code, ExitCode.HOLD)


class TestGateReleasePrecedence(unittest.TestCase):
    """A gate command outranks a pending consult and clears the lock."""

    def test_approve_gate_routes_forward_even_with_a_pending_consult(self):
        state = _gated_state(function_call=_consult_call("context"))
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                state, parse_workflow_command("/nebuko approve gate"), HUMAN, tmpdir
            )
        self.assertNotEqual(cfg.get("SKIP_AGENT"), "true")
        # The decision wins over the pending consult: the workflow advances to
        # the approved successor, and the lock is released.
        self.assertEqual(cfg["task"], "dev")
        self.assertEqual(cfg["config_step"], "implement")
        self.assertEqual(cfg["config_gate_released"], "true")

    def test_bare_retrigger_on_the_armed_gate_is_a_hold_not_a_failure(self):
        """The reported exit-20 repro: a re-trigger holds benignly (10)."""
        state = _gated_state()
        cfg = _resolve_resume_path(state, "resume", "/nonexistent", human_input="")
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        skip = SkipOutcome.from_verdict(cfg)
        self.assertEqual(skip.exit_code, ExitCode.HOLD)

    def test_bot_retrigger_on_the_armed_gate_holds_with_the_typed_kind(self):
        """The state-only bot path carries the same benign verdict."""
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import (
            _resolve_bot_fallback_path,
        )

        state = _gated_state()
        body = "\n".join(
            [
                "<!-- bot-trigger: architect -->",
                "<!-- WORKFLOW_STATE_START -->",
                state.model_dump_json(),
                "<!-- WORKFLOW_STATE_END -->",
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_bot_fallback_path(body, tmpdir)
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertEqual(cfg.get("SKIP_KIND"), "human_gate_hold")
        self.assertEqual(SkipOutcome.from_verdict(cfg).exit_code, ExitCode.HOLD)

    def test_unauthorized_author_cannot_release_the_gate(self):
        state = _gated_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            cfg = _resolve_gate_decision_path(
                state,
                parse_workflow_command("/nebuko approve gate"),
                AuthorIdentity(
                    login="random",
                    is_bot=False,
                    association="NONE",
                    is_machine_comment=False,
                ),
                tmpdir,
            )
        self.assertEqual(cfg.get("SKIP_AGENT"), "true")
        self.assertNotEqual(cfg.get("config_gate_released"), "true")


class TestGateArmingFromTheWorkflowDefinition(unittest.TestCase):
    """The gate is a step-config fact: ``human_gate: true`` arms it at root."""

    def test_command_parsing_is_not_confused_by_a_pending_consult(self):
        cmd = parse_workflow_command("/nebuko approve gate")
        self.assertEqual(cmd.action, "approve")

    def test_gated_step_in_the_yaml_declares_the_gate(self):
        from nebuko.engine.lib.config import load_workflow

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(_write_workflow(tmpdir), "default.yaml")
            wf = load_workflow(path)
        gated = [s.name for s in wf.steps if getattr(s, "human_gate", False)]
        self.assertEqual(gated, ["plan"])


if __name__ == "__main__":
    unittest.main()
