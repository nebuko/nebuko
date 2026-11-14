"""Engine-owned human gate decision record (issue #648).

A ``GateDecision`` is the only artifact that can move a workflow past a
``human_gate`` step. It is written by the engine from an identity-verified
human command (``/nebuko approve gate`` / ``/nebuko reject gate``), never
derived from agent-writable comment content.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from nebuko.engine.lib.config import StepRef


class GateDecision(BaseModel):
    """Immutable, attributed record that a human decided a specific gate.

    Fields:
        status: The human's decision — ``approved`` or ``rejected``.
        workflow: Workflow file name owning the gated step.
        target_step: Name of the gated step the decision applies to.
        decided_by: GitHub login of the deciding human.
        decided_at: Timestamp of the decision.
        feedback: Optional free-text the human attached to the decision,
            injected into the routed step's context.
        comment_id: Optional id of the deciding comment (audit link).
        position: Flat workflow step path of the consumed hold instance.
            Binds the decision to exactly one hold so it cannot be replayed
            onto a later gate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["approved", "rejected"]  # @stub-protected
    workflow: str  # @stub-protected
    target_step: str  # @stub-protected
    decided_by: str
    decided_at: datetime  # @stub-protected
    feedback: str = ""
    comment_id: Optional[int] = None
    position: list[StepRef] = Field(default_factory=list)

    def is_for(
        self, workflow: str, step: str, position: list[StepRef]
    ) -> bool:  # @stub-protected
        """True iff the decision targets that exact (workflow, step, position)."""
        return (
            self.workflow == workflow
            and self.target_step == step
            and self.position == position
        )
