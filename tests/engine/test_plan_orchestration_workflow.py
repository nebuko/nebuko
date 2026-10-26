"""Shipped-config regression guards for plan-orchestration (issue #784).

The failure this pins: ``plan-orchestration`` promised a human approval gate at
its ``plan`` step and a "materialize the plan only" ``scaffold`` step, but the
control-flow definition carried neither, so execution ran with no plan artifact
and no review. These tests assert the shipped YAML keeps both structural.
"""

from __future__ import annotations

from pathlib import Path

from nebuko.engine.lib.config import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / "defaults" / "workflows"


def _steps(workflow_name: str) -> dict:
    definition = load_workflow(str(WORKFLOWS / workflow_name))
    return {step.name: step for step in definition.steps}


class TestPlanOrchestrationGate:
    def test_plan_step_is_a_human_gate(self):
        assert _steps("plan-orchestration.yaml")["plan"].human_gate is True

    def test_spec_driven_gates_remain_intact(self):
        assert _steps("spec-driven.yaml")["spec"].human_gate is True
        assert _steps("spec-driven-impl.yaml")["stub_approval"].human_gate is True


class TestScaffoldScopeContract:
    def test_scaffold_replaces_the_role_prompt(self):
        scaffold = _steps("plan-orchestration.yaml")["scaffold"]
        assert scaffold.prompt_replace is True
        assert scaffold.prompt and scaffold.prompt.strip()

    def test_scaffold_no_longer_carries_implementation_flow(self):
        scaffold = _steps("plan-orchestration.yaml")["scaffold"]
        assert scaffold.skill is None
        assert scaffold.next == "scaffold_review"

    def test_scaffold_review_loops_back_to_scaffold_on_rejection(self):
        review = _steps("plan-orchestration.yaml")["scaffold_review"]
        routes = {condition.on_result: condition.next for condition in review.conditions}
        assert routes == {"approved": "sub_impl", "rejected": "scaffold"}
