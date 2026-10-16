"""Spec tests for the WorkflowPosition sub-domain (issue #638, v3).

Scenarios from the approved plan:
  1. Flat step-path data class: fields, `leaf()`, mount-chain address,
     append/parent immutability.
  2. v3 state shape: `to_state_dict()` flat `refs` block + leaf aliases;
     `from_state_dict()` accepts v1 (bare pair), v2 (recursive) and
     v3 (flat) input; round-trip.
  3. #617 regression: the `resolve_position` builder resolves the full
     chain plan-orchestration / sub_impl -> tmp-impl / step_1
     -> dev-review-loop / implement (leaf), not the parent's `scaffold`.
  4. Self function_call neutralization helper (defect 2).

Builder tests (class `TestResolvePositionBuilder`) are the acceptance gate
for issue #617/#618 - they resolve the full mount chain through
`resolve_position` and must pass against the real builder.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import (  # noqa: E402
    POSITION_STATE_VERSION,
    StepRef,
    WorkflowDefinition,
    WorkflowPosition,
    WorkflowStep,
    is_self_function_call,
)


def _wf(name: str, *steps: WorkflowStep) -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name,
        description=name,
        steps=list(steps),
    )


# -- #617 scenario fixtures ---------------------------------------------
PLAN_ORCHESTRATION = _wf(
    "plan-orchestration",
    WorkflowStep(name="architect", agent="architect", next="context"),
    WorkflowStep(name="context", agent="context", next="plan"),
    WorkflowStep(name="plan", agent="architect", next="scaffold"),
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

_CHAIN = [
    StepRef(workflow="plan-orchestration", step="sub_impl"),
    StepRef(workflow="tmp-impl", step="step_1"),
    StepRef(workflow="dev-review-loop", step="implement"),
]


class TestStepRef:
    def test_value_object(self):
        ref = StepRef(workflow="dev-review-loop", step="implement")
        assert ref.workflow == "dev-review-loop"
        assert ref.step == "implement"
        assert ref.model_dump() == {"workflow": "dev-review-loop", "step": "implement"}


class TestWorkflowPositionModel:
    def test_minimal_position_is_its_own_leaf(self):
        pos = WorkflowPosition(refs=[StepRef(workflow="default", step="analyze")])
        assert pos.leaf().workflow == "default"
        assert pos.leaf().step == "analyze"
        assert pos.leaf_workflow() == "default"
        assert pos.leaf_step() == "analyze"

    def test_leaf_returns_innermost_ref(self):
        root = WorkflowPosition(refs=_CHAIN)
        assert root.leaf() is _CHAIN[2]
        mid = root.parent()
        assert mid is not None
        assert mid.leaf() is _CHAIN[1]
        bare = mid.parent()
        assert bare is not None
        assert bare.leaf() is _CHAIN[0]

    def test_empty_path_has_no_leaf(self):
        pos = WorkflowPosition()
        assert pos.leaf() is None
        assert pos.leaf_workflow() == ""
        assert pos.leaf_step() == ""
        assert pos.parent() is None

    def test_path_str_is_outermost_first(self):
        pos = WorkflowPosition(refs=_CHAIN)
        assert pos.path_str() == (
            "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/implement"
        )
        assert WorkflowPosition().path_str() == ""

    def test_append_and_parent_are_immutable(self):
        base = WorkflowPosition(refs=_CHAIN[:2])
        extended = base.with_appended(StepRef(workflow="dev-review-loop", step="implement"))
        assert extended.refs == _CHAIN
        assert base.refs == _CHAIN[:2]  # untouched
        assert extended.parent() is not None
        assert extended.parent().refs == _CHAIN[:2]

    def test_to_state_dict_is_v3_flat_shape_with_leaf_aliases(self):
        pos = WorkflowPosition(refs=_CHAIN)
        d = pos.to_state_dict()
        assert d["version"] == POSITION_STATE_VERSION == 3
        assert d["refs"] == [
            {"workflow": "plan-orchestration", "step": "sub_impl"},
            {"workflow": "tmp-impl", "step": "step_1"},
            {"workflow": "dev-review-loop", "step": "implement"},
        ]
        # Flat leaf aliases keep v1 consumers pointed at the executed step.
        assert d["workflow"] == "dev-review-loop"
        assert d["step"] == "implement"

    def test_empty_position_state_dict_has_no_refs(self):
        d = WorkflowPosition().to_state_dict()
        assert d == {"version": POSITION_STATE_VERSION}

    def test_from_state_dict_v3_round_trip(self):
        pos = WorkflowPosition(refs=_CHAIN)
        assert WorkflowPosition.from_state_dict(pos.to_state_dict()) == pos

    def test_from_state_dict_converts_legacy_v2_nested(self):
        v2 = {
            "version": 2,
            "workflow_name": "plan-orchestration",
            "step_name": "sub_impl",
            "step": {
                "version": 2,
                "workflow_name": "tmp-impl",
                "step_name": "step_1",
                "step": {
                    "version": 2,
                    "workflow_name": "dev-review-loop",
                    "step_name": "implement",
                },
            },
        }
        pos = WorkflowPosition.from_state_dict(v2)
        assert pos is not None
        assert pos.refs == _CHAIN

    def test_from_state_dict_accepts_v1_bare_pair(self):
        pos = WorkflowPosition.from_state_dict(
            {"workflow": "default", "step": "analyze"}
        )
        assert pos is not None
        assert pos.refs == [StepRef(workflow="default", step="analyze")]

    def test_from_state_dict_none_and_empty(self):
        assert WorkflowPosition.from_state_dict(None) is None
        assert WorkflowPosition.from_state_dict({}) is None

    def test_from_env_round_trip(self):
        raw = json.dumps(WorkflowPosition(refs=_CHAIN).to_state_dict())
        pos = WorkflowPosition.from_env(raw)
        assert pos is not None
        assert pos.refs == _CHAIN
        assert WorkflowPosition.from_env("") is None
        assert WorkflowPosition.from_env("{not json") is None


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
    """v3 state: position is a flat refs list; v1 states still parse."""

    def test_state_serializes_position_refs(self):
        from workflow_state import WorkflowState
        state = WorkflowState(
            workflow="dev-review-loop",  # flat fields keep leaf (v1) semantics
            step="implement",
            agent="dev",
            position=_CHAIN,
        )
        dumped = state.model_dump()
        assert dumped["position"] == [r.model_dump() for r in _CHAIN]
        assert dumped["workflow"] == "dev-review-loop"
        assert dumped["step"] == "implement"

    def test_v1_state_without_position_still_validates(self):
        from workflow_state import WorkflowState
        state = WorkflowState.model_validate(
            {"workflow": "plan-orchestration", "step": "scaffold", "agent": "dev"}
        )
        assert state.position == []

    def test_position_field_defaults_to_empty_list(self):
        from workflow_state import WorkflowState
        assert WorkflowState().position == []

    def test_legacy_v2_nested_block_is_migrated_on_load(self):
        from workflow_state import WorkflowState
        v2_block = {
            "version": 2,
            "workflow_name": "plan-orchestration",
            "step_name": "sub_impl",
            "step": {
                "version": 2,
                "workflow_name": "dev-review-loop",
                "step_name": "implement",
            },
        }
        state = WorkflowState.model_validate(
            {"workflow": "dev-review-loop", "step": "implement", "agent": "dev",
             "position": v2_block}
        )
        assert state.position == [
            StepRef(workflow="plan-orchestration", step="sub_impl"),
            StepRef(workflow="dev-review-loop", step="implement"),
        ]

    def test_history_entry_carries_position(self):
        from workflow_state import Appreciation, HistoryEntry, TurnOutcome, TurnStatus

        def _outcome():
            return TurnOutcome(
                status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED
            )
        entry = HistoryEntry(step="implement", agent="dev",
                             summary="ran", position=_CHAIN,
                             turn_outcome=_outcome())
        dumped = entry.model_dump()
        assert dumped["position"] == [r.model_dump() for r in _CHAIN]
        legacy = HistoryEntry.model_validate(
            {
                "step": "s",
                "agent": "a",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
            }
        )
        assert legacy.position == []


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
        assert pos.leaf().workflow == "dev-review-loop"
        assert pos.leaf().step == "implement"
        # Full chain recorded outermost-first, not just the leaf.
        assert pos.refs == _CHAIN

    def test_callee_from_scaffold_resolves_to_implement_not_scaffold(self):
        """The #617 dead-end: dev handed off from `scaffold`; the callee step
        must be the sub-workflow leaf, never the already-consumed `scaffold`."""
        pos = self._resolve(PLAN_ORCHESTRATION, "sub_impl")
        assert pos.leaf().workflow != "plan-orchestration"
        assert pos.leaf().step != "scaffold"

    def test_non_mount_step_is_leaf_itself(self):
        pos = self._resolve(DEV_REVIEW_LOOP, "implement")
        assert pos.leaf() is pos.refs[-1]
        assert pos.refs == [StepRef(workflow="dev-review-loop", step="implement")]
