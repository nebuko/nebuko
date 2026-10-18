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

sys.path.insert(0, str(Path(__file__).parents[3]))

from config import StepRef  # noqa: E402

from resolve_config import resolve_config  # noqa: E402
from resolve_config.features.resolve_route.resolve_route import (  # noqa: E402
    _armed_gate,
    _resolve_gate_consult,
    _resolve_gate_decision_path,
    _resolve_handoff_decision,
    _resolve_outcome_path,
    _resolve_resume_path,
)
from resolve_config.sub_domains.author_identity.author_identity import (  # noqa: E402
    AuthorIdentity,
)
from resolve_config.sub_domains.workflow_command.workflow_command import (  # noqa: E402
    parse_workflow_command,
)
from nebuko.engine.models import ExitCode, SkipKind, SkipOutcome  # noqa: E402
from workflow_state import (  # noqa: E402
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
            appreciation=Appreciation.HOLD,
            hold_kind=HoldKind.AWAITING_APPROVAL,
            note=f"human_gate:{step}",
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
        self.assertNotEqual(decision.reason_code, "gate_hold")
        self.assertEqual(decision.target_agent, "context")

    def test_hold_without_an_armed_gate_still_wins_over_the_handoff(self):
        """Regression guard: an unarmed gate hold keeps its #887 precedence."""
        state = _gated_state(
            gate=GateLock(step="plan", status=GateStatus.RELEASED),
            function_call=_consult_call("context"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir)
            decision = _resolve_handoff_decision(state, tmpdir)
        self.assertEqual(decision.reason_code, "gate_hold")

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
        from resolve_config.features.resolve_route.resolve_route import (
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
        from config import load_workflow

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(_write_workflow(tmpdir), "default.yaml")
            wf = load_workflow(path)
        gated = [s.name for s in wf.steps if getattr(s, "human_gate", False)]
        self.assertEqual(gated, ["plan"])


if __name__ == "__main__":
    unittest.main()
