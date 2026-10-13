"""Hand-off scope classifier (issue #889).

The single source of truth for one question the producer (``build_output``)
and the resolver (``resolve_route``) must answer identically: *does a pending
agent-to-agent hand-off advance the workflow, or is it an out-of-workflow
sub-run that a human gate must let through?*

A human gate holds workflow **advancement** (issue #887). It must not swallow
an explicit out-of-workflow sub-run — the case where the gated ``plan`` step
calls out to ``context`` for more knowledge before it can finish. Producer and
resolver used to decide this independently (the producer wiped every pending
hand-off on a gated step; the resolver short-circuited any ``human_gate`` hold
to ``gate_hold``), so a gate could silently drop the sub-run. Classifying once
here keeps the two sides from diverging (issue #889 root cause).
"""

from typing import Literal, Optional

from pydantic import BaseModel

from ..workflow_position.workflow_position import WorkflowPosition

# The typed ``call_agent`` marker persisted on a FunctionCall. A hand-off whose
# ``function`` is anything else (today: a ``dispatch_next`` target promoted to
# a call_agent spec) is a *workflow dispatch*, not an out-of-workflow sub-run
# (issue #889 R1).
SUB_RUN_FUNCTION = "call_agent"


class HandOffScope(BaseModel):
    """How a pending hand-off relates to the current workflow position (#889).

    ``kind`` is the routing decision:

    - ``sub_run`` — a position-preserving out-of-workflow hand-off (an explicit
      typed ``call_agent``). The callee runs and control returns to the caller;
      a human gate must let it through.
    - ``transition`` — a position-advancing routing (a workflow dispatch, e.g.
      ``dispatch_next``/``advance``, or a fallback hand-off). On a gated step it
      stays held.
    - ``none`` — no pending hand-off to classify.

    Attributes:
        kind: The routing decision (see above).
        preserves_position: True exactly for ``sub_run`` — the callee does not
            move the workflow position.
        callee_agent: The named agent to run, when known.
        callee_step: The step the callee should run, when known.
        caller_position: The position to return to when the callee finishes
            (the classifying position), or None when there is no position.
    """

    kind: Literal["none", "sub_run", "transition"]
    preserves_position: bool
    callee_agent: Optional[str] = None
    callee_step: Optional[str] = None
    caller_position: Optional[WorkflowPosition] = None

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def classify(
        cls,
        function_call=None,
        position: Optional[WorkflowPosition] = None,
        dispatch=None,
        workflow=None,
    ) -> "HandOffScope":
        """Classify a pending hand-off on ``position`` (see module docstring).

        The discriminator (issue #889 R1) is provenance on the ``FunctionCall``:
        ``sub_run`` is set only for an **explicit typed** ``call_agent`` that
        was the run's own decision (no ``dispatch_next`` sibling), whereas a
        ``dispatch_next`` target promoted to a call_agent spec is a *workflow
        dispatch* and classifies as ``transition``.

        Args:
            function_call: The pending ``FunctionCall`` from state, if any.
            position: The workflow position the hand-off is pending on.
            dispatch: The accompanying dispatch, if any (reserved for the
                producer's hold-consistency reasoning; not a discriminator).
            workflow: The active workflow definition, if any (reserved for
                callee-step resolution by the caller; not a discriminator).

        Returns:
            A ``HandOffScope``. ``sub_run`` iff the hand-off is an explicit
            typed ``call_agent`` (``FunctionCall.sub_run`` set) with a named
            agent; otherwise ``transition`` when a hand-off is present, or
            ``none`` when it is absent.
        """
        if function_call is None:
            return cls(kind="none", preserves_position=False)
        callee = (getattr(function_call, "agent_name", "") or "").strip() or None
        is_sub_run = (
            getattr(function_call, "function", "") == SUB_RUN_FUNCTION
            and bool(getattr(function_call, "sub_run", False))
        )
        if is_sub_run and callee:
            return cls(
                kind="sub_run",
                preserves_position=True,
                callee_agent=callee,
                caller_position=position,
            )
        return cls(
            kind="transition",
            preserves_position=False,
            callee_agent=callee,
            caller_position=position,
        )
