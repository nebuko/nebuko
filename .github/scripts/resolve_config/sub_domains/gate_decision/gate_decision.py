"""GateDecision — persisted record of a human's out-of-band gate decision (issue #648)."""

from typing import Literal, Optional

from pydantic import BaseModel

__all__ = ["GateDecision"]


class GateDecision(BaseModel):
    """Immutable persisted record of a human's out-of-band gate decision.

    Written by the workflow engine when a human issues ``/workflow approve gate``
    or ``/workflow reject gate`` against a held ``human_gate`` step, and stored
    in ``WorkflowState.gate`` so the release is deterministic and auditable.

    Fields:
        decision: The human's verdict ("approved" or "rejected").
        gate_step: Name of the gated step that was on hold.
        approver: GitHub login that issued the command.
        decided_at: ISO-8601 UTC timestamp of the decision.
        reason: Optional free-text ``--reason`` supplied with the command.
    """

    decision: Literal["approved", "rejected"]
    gate_step: str
    approver: str
    decided_at: str
    reason: Optional[str] = None
