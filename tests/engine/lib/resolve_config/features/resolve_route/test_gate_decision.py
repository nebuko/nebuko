"""Gate verdict routing at a gated step (issue #1274).

RED specs: a rejected agent verdict at a ``human_gate`` step that declares an
``on_result: rejected`` conditional edge must auto-route that edge (including
backward edges) instead of parking in ``human_gate_hold``. Approved verdicts
and gates with no rejected edge keep the existing hold behavior.
"""

from nebuko.engine.lib.resolve_config.features.resolve_route import (
    resolve_route as rr,
)
from nebuko.engine.lib.workflow_state import GateLock, GateStatus  # noqa: F401
from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

GATED_WF = """\
name: gated
description: plan -> implement with a human gate on plan
steps:
  - name: context
    agent: context
    next: plan
  - name: plan
    agent: architect
    human_gate: true
    next: implement
    conditions:
      - on_result: approved
        next: implement
      - on_result: rejected
        next: context
"""


def _gated_state(appreciation: str, workflow: str = "gated") -> WorkflowState:
    return WorkflowState.model_validate(
        {
            "version": 7,
            "workflow": workflow,
            "step": "plan",
            "agent": "corrupted",
            "gate": {"step": "plan", "status": "armed"},
            "turn_outcome": {"status": "completed", "appreciation": appreciation},
        }
    )


def test_rejected_with_declared_reject_edge_routes_reject_branch(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "gated.yaml").write_text(GATED_WF)
    state = _gated_state("rejected")
    config = rr._gate_reject_verdict_route(
        state, state.gate, state.turn_outcome, str(wf_dir), None
    )
    assert config is not None
    assert config["config_step"] == "context"


def test_rejected_without_reject_edge_falls_back_to_human_hold(tmp_path):
    wf_no_reject = GATED_WF.replace(
        "      - on_result: rejected\n        next: context\n", ""
    )
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "gated.yaml").write_text(wf_no_reject)
    state = _gated_state("rejected")
    assert not rr._gate_step_has_rejected_edge("plan", "gated", str(wf_dir), None)
    assert state.gate.armed()


def test_approved_verdict_keeps_human_hold(tmp_path):
    state = _gated_state("approved")
    assert rr._armed_gate(state) is not None
    result = rr._human_gate_hold_result("plan")
    assert result["SKIP_KIND"] == "human_gate_hold"


def test_release_armed_gate_consumes_lock(tmp_path):
    state = _gated_state("rejected")
    armed = rr._armed_gate(state)
    assert armed is not None
    rr._release_armed_gate(state, armed)
    assert not state.gate.armed()
