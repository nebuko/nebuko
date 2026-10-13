"""Spec tests for the HandOffScope classifier (issue #889).

The classifier is the single source of truth both the producer
(``build_output``) and the resolver (``resolve_route``) consult, so producer
and resolver can never disagree on whether a pending hand-off advances the
workflow. The matrix:

* no hand-off                -> ``none``
* explicit typed ``call_agent`` -> ``sub_run`` (position-preserving)
* ``dispatch_next`` promotion  -> ``transition`` (position-advancing)
* unresolvable hand-off        -> ``transition``
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import HandOffScope, StepRef, WorkflowPosition  # noqa: E402
from workflow_state import FunctionCall  # noqa: E402


def _position(workflow: str = "default", step: str = "plan") -> WorkflowPosition:
    return WorkflowPosition.bare(workflow, step)


class TestHandOffScopeClassify:
    """The classify() matrix (issue #889)."""

    def test_no_hand_off_is_none(self):
        scope = HandOffScope.classify(function_call=None, position=_position())
        assert scope.kind == "none"
        assert scope.preserves_position is False
        assert scope.callee_agent is None

    def test_explicit_typed_call_agent_is_sub_run(self):
        fc = FunctionCall(function="call_agent", agent_name="context", sub_run=True)
        scope = HandOffScope.classify(function_call=fc, position=_position())
        assert scope.kind == "sub_run"
        assert scope.preserves_position is True
        assert scope.callee_agent == "context"
        assert scope.caller_position is not None
        assert scope.caller_position.leaf_step() == "plan"

    def test_dispatch_promotion_is_transition(self):
        # A dispatch_next target promoted to a call_agent spec carries no
        # explicit-typed provenance, so it is a workflow dispatch (R1).
        fc = FunctionCall(function="call_agent", agent_name="context")
        assert fc.sub_run is False
        scope = HandOffScope.classify(function_call=fc, position=_position())
        assert scope.kind == "transition"
        assert scope.preserves_position is False
        assert scope.callee_agent == "context"

    def test_unresolvable_hand_off_is_transition(self):
        # Explicit provenance but no named agent -> cannot be a sub-run.
        fc = FunctionCall(function="call_agent", agent_name="context", sub_run=True)
        fc = fc.model_copy(update={"agent_name": ""})
        scope = HandOffScope.classify(function_call=fc, position=_position())
        assert scope.kind == "transition"

    def test_sub_run_without_position_still_classifies(self):
        fc = FunctionCall(function="call_agent", agent_name="context", sub_run=True)
        scope = HandOffScope.classify(function_call=fc, position=None)
        assert scope.kind == "sub_run"
        assert scope.caller_position is None


class TestFunctionCallSubRunProvenance:
    """The provenance flag round-trips through serialized state (issue #889)."""

    def test_default_false_survives_round_trip_as_false(self):
        dumped = FunctionCall(function="call_agent", agent_name="dev").model_dump(
            exclude_defaults=True
        )
        assert "sub_run" not in dumped
        assert FunctionCall.model_validate(dumped).sub_run is False

    def test_true_survives_round_trip(self):
        dumped = FunctionCall(
            function="call_agent", agent_name="context", sub_run=True
        ).model_dump(exclude_defaults=True)
        assert dumped["sub_run"] is True
        assert FunctionCall.model_validate(dumped).sub_run is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
