"""Spec tests for the WorkflowPosition sub-domain (issue #618).

Scenarios from the approved plan:
  1. Recursive data class: fields, `leaf` property, mount-chain address.
  2. v2 state shape: `to_state_dict()` nested block + flat leaf aliases;
     `from_state_dict()` round-trip; v1 state (no position key) still
     parses.
  3. #617 regression: position builder resolves the full chain
     plan-orchestration / sub_impl -> tmp-impl / step_1
     -> dev-review-loop / implement (leaf), not the parent's `scaffold`.
  4. Self function_call neutralization helper (defect 2).

Builder tests (class `TestResolvePositionBuilder`) are the acceptance gate
for issue #618 — they resolve the full mount chain through `resolve_position`
and must pass against the real builder.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import (  # noqa: E402
    POSITION_STATE_VERSION,
    WorkflowDefinition,
    WorkflowPosition,
    WorkflowStep,
    WorkflowTrigger,
    is_self_function_call,
)


def _wf(name: str, *steps: WorkflowStep) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name,
        description=name,
        trigger=WorkflowTrigger(agents=["dev"]),
        steps=list(steps),
    )


# ── #617 scenario fixtures ────────────────────────────────────────────
PLAN_ORCHESTRATION = _wf(
    "plan-orchestration",
    WorkflowStep(name="architect", agent="genius", next="context"),
    WorkflowStep(name="context", agent="ctx", next="plan"),
    WorkflowStep(name="plan", agent="genius", next="scaffold"),
    WorkflowStep(name="scaffold", agent="dev", next="sub_impl"),
    WorkflowStep(name="sub_impl", workflow="tmp_impl/workflow.yaml", next="sub_clean"),
    WorkflowStep(name="sub_clean", agent="dev", next=None),
)
TMP_IMPL = _wf(
    "tmp-impl",
    WorkflowStep(name="step_1", workflow="dev-review-loop", next="step_2"),
    WorkflowStep(name="step_2", workflow="dev-review-loop", next=None),
)
DEV_REVIEW_LOOP = _wf(
    "dev-review-loop",
    WorkflowStep(name="implement", agent="dev", next="review"),
    WorkflowStep(name="review", agent="review", next=None),
)


class TestWorkflowPositionModel:
    def test_minimal_position_is_its_own_leaf(self):
        pos = WorkflowPosition(workflow_name="default", step_name="analyze")
        assert pos.sub is None
        assert pos.leaf is pos
        assert pos.workflow_path == ""

    def test_leaf_returns_innermost_position(self):
        leaf = WorkflowPosition(workflow_name="dev-review-loop", step_name="implement")
        mid = WorkflowPosition(
            workflow_name="tmp-impl", workflow_path="tmp_impl/workflow.yaml",
            step_name="step_1", sub=leaf,
        )
        root = WorkflowPosition(
            workflow_name="plan-orchestration",
            workflow_path="workflows/plan-orchestration.yaml",
            step_name="sub_impl", sub=mid,
        )
        assert root.leaf == leaf
        assert mid.leaf == leaf
        assert leaf.leaf is leaf

    def test_position_path_lists_chain_outermost_first(self):
        leaf = WorkflowPosition(workflow_name="dev-review-loop", step_name="implement")
        mid = WorkflowPosition(workflow_name="tmp-impl", step_name="step_1", sub=leaf)
        root = WorkflowPosition(workflow_name="plan-orchestration", step_name="sub_impl", sub=mid)
        assert root.position_path() == (
            ("plan-orchestration", "sub_impl"),
            ("tmp-impl", "step_1"),
            ("dev-review-loop", "implement"),
        )

    def test_to_state_dict_is_v2_nested_shape_with_leaf_aliases(self):
        pos = WorkflowPosition(
            workflow_name="plan-orchestration",
            workflow_path="workflows/plan-orchestration.yaml",
            step_name="sub_impl",
            sub=WorkflowPosition(
                workflow_name="tmp-impl",
                workflow_path="tmp_impl/workflow.yaml",
                step_name="step_1",
                sub=WorkflowPosition(
                    workflow_name="dev-review-loop",
                    workflow_path="workflows/dev-review-loop.yaml",
                    step_name="implement",
                ),
            ),
        )
        d = pos.to_state_dict()
        assert d["version"] == POSITION_STATE_VERSION == 2
        assert d["workflow_name"] == "plan-orchestration"
        assert d["step_name"] == "sub_impl"
        # Flat leaf aliases keep v1 consumers pointed at the executed step.
        assert d["leaf_workflow"] == "dev-review-loop"
        assert d["leaf_step"] == "implement"
        # Nested chain: inner level under "step", exactly the user's sketch.
        inner = d["step"]
        assert inner["workflow_name"] == "tmp-impl"
        assert inner["step_name"] == "step_1"
        assert inner["leaf_workflow"] == "dev-review-loop"
        assert inner["step"]["workflow_name"] == "dev-review-loop"
        assert inner["step"]["step_name"] == "implement"
        assert "step" not in inner["step"]

    def test_leaf_position_state_dict_has_no_nested_step_key(self):
        pos = WorkflowPosition(workflow_name="default", step_name="analyze")
        d = pos.to_state_dict()
        assert "step" not in d
        assert d["leaf_workflow"] == "default"
        assert d["leaf_step"] == "analyze"

    def test_from_state_dict_round_trip(self):
        pos = WorkflowPosition(
            workflow_name="plan-orchestration",
            workflow_path="workflows/plan-orchestration.yaml",
            step_name="sub_impl",
            sub=WorkflowPosition(workflow_name="tmp-impl", step_name="step_1",
                                 sub=WorkflowPosition(workflow_name="dev-review-loop",
                                                      step_name="implement")),
        )
        assert WorkflowPosition.from_state_dict(pos.to_state_dict()) == pos

    def test_from_state_dict_none_and_empty(self):
        assert WorkflowPosition.from_state_dict(None) is None
        assert WorkflowPosition.from_state_dict({}) is None

    def test_is_self_call(self):
        pos = WorkflowPosition(workflow_name="dev-review-loop", step_name="implement")
        assert pos.is_self_call("dev", "dev") is True
        assert pos.is_self_call("review", "dev") is False
        assert pos.is_self_call("", "dev") is False


class TestIsSelfFunctionCall:
    def test_matches_only_the_running_agent(self):
        from workflow_state import FunctionCall
        fc = FunctionCall.model_validate({"agent_name": "dev", "complexity": "medium"})
        assert is_self_function_call(fc, "dev") is True
        assert is_self_function_call(fc, "review") is False

    def test_none_or_empty_running_agent_is_not_self(self):
        from workflow_state import FunctionCall
        fc = FunctionCall.model_validate({"agent_name": "dev"})
        assert is_self_function_call(fc, "") is False
        assert is_self_function_call(None, "dev") is False


class TestWorkflowStatePositionField:
    """v2 state: position block is additive; v1 states still parse."""

    def test_state_serializes_position_block(self):
        from workflow_state import WorkflowState
        pos = WorkflowPosition(
            workflow_name="plan-orchestration", step_name="sub_impl",
            sub=WorkflowPosition(workflow_name="dev-review-loop", step_name="implement"),
        )
        state = WorkflowState(
            workflow="dev-review-loop",  # flat fields keep leaf (v1) semantics
            step="implement",
            agent="dev",
            position=pos.to_state_dict(),
        )
        dumped = state.model_dump()
        assert dumped["position"]["leaf_step"] == "implement"
        assert dumped["position"]["step"]["workflow_name"] == "dev-review-loop"
        assert dumped["workflow"] == "dev-review-loop"
        assert dumped["step"] == "implement"

    def test_v1_state_without_position_still_validates(self):
        from workflow_state import WorkflowState
        state = WorkflowState.model_validate(
            {"workflow": "plan-orchestration", "step": "scaffold", "agent": "dev"}
        )
        assert state.position is None

    def test_position_field_is_optional_default_none(self):
        from workflow_state import WorkflowState
        assert WorkflowState().position is None


class TestResolvePositionBuilder:
    """Acceptance gate (issue #617 regression): the builder must resolve the
    full mount chain through ``resolve_position``."""

    def _resolve(self, wf_def: WorkflowDefinition, step_name: str) -> WorkflowPosition:
        from resolve_config.features.resolve_route.resolve_route import resolve_position
        table = {
            "plan-orchestration": PLAN_ORCHESTRATION,
            "tmp_impl/workflow.yaml": TMP_IMPL,
            "dev-review-loop": DEV_REVIEW_LOOP,
        }
        # Port: resolve a mount reference to its WorkflowDefinition.
        resolve_ref = lambda ref: table.get(ref)  # noqa: E731
        return resolve_position(wf_def, step_name, resolve_ref=resolve_ref,
                                workflow_path="workflows/plan-orchestration.yaml")

    def test_full_mount_chain_leaves_at_dev_review_loop_implement(self):
        pos = self._resolve(PLAN_ORCHESTRATION, "sub_impl")
        assert pos.workflow_name == "plan-orchestration"
        assert pos.step_name == "sub_impl"
        assert pos.leaf.workflow_name == "dev-review-loop"
        assert pos.leaf.step_name == "implement"
        # Full chain recorded, not just the leaf (diagnostic value).
        assert pos.position_path() == (
            ("plan-orchestration", "sub_impl"),
            ("tmp-impl", "step_1"),
            ("dev-review-loop", "implement"),
        )

    def test_callee_from_scaffold_resolves_to_implement_not_scaffold(self):
        """The #617 dead-end: dev handed off from `scaffold`; the callee step
        must be the sub-workflow leaf, never the already-consumed `scaffold`."""
        pos = self._resolve(PLAN_ORCHESTRATION, "sub_impl")
        assert pos.leaf.workflow_name != "plan-orchestration"
        assert pos.leaf.step_name != "scaffold"

    def test_non_mount_step_is_leaf_itself(self):
        pos = self._resolve(DEV_REVIEW_LOOP, "implement")
        assert pos.leaf == pos
        assert pos.step_name == "implement"
