"""Root-level human-gate lock (issue #1003).

The gate is *workflow metadata*, not a step verdict: entering an armed
``human_gate`` step raises a lock at the root of the workflow state, and only
an identity-verified ``/nebuko approve gate`` / ``/nebuko reject gate`` command
releases it. Keeping the lock independent of ``position`` and of any step's
``turn_outcome`` is what lets a gated agent still end its turn with a real
verdict *and* still delegate to another agent (``call_agent``): the lock blocks
the workflow *advance*, never an agent-to-agent call.
"""

from enum import Enum
from typing import Mapping, Optional

from pydantic import BaseModel, ConfigDict

# Bound on how many times a gated step may consult another agent while the
# lock stays armed (issue #1003). A consultation is not an advance, so it must
# be allowed — but it must also be bounded, otherwise a gated step could keep
# fanning out runs forever without a human ever approving.
MAX_CONSULT_ROUNDS = 3


class GateStatus(str, Enum):  # @stub-protected
    """Whether the root gate currently blocks the workflow advance."""

    ARMED = "armed"
    RELEASED = "released"


class GateLock(BaseModel):  # @stub-protected
    """Workflow-level lock raised when an armed ``human_gate`` step is entered.

    Blocks workflow advance only; never blocks ``call_agent``.

    Fields:
        step: Gated step name (e.g. ``plan``).
        status: ``armed`` while a human decision is owed, ``released`` once a
            gate command (or a config edit) cleared the lock.
        consult_rounds: ``call_agent`` consultations consumed while armed.
        last_handoff: Callee of the most recent consult (audit/note only).
    """

    model_config = ConfigDict(extra="forbid")

    step: str = ""
    status: GateStatus = GateStatus.ARMED
    consult_rounds: int = 0
    last_handoff: Optional[str] = None

    def armed(self) -> bool:  # @stub-protected
        """True while the lock blocks the workflow advance."""
        return self.status is GateStatus.ARMED

    def can_consult(self) -> bool:  # @stub-protected
        """True while this gated step may still delegate to another agent."""
        return self.armed() and self.consult_rounds < MAX_CONSULT_ROUNDS

    def consume_consult(self, agent: str) -> "GateLock":  # @stub-protected
        """Return the lock with one consultation spent on ``agent``.

        Immutable replace (mirrors ``SoftRetryState.record``) so callers never
        mutate shared state.
        """
        return self.model_copy(
            update={
                "consult_rounds": self.consult_rounds + 1,
                "last_handoff": (agent or "").strip() or None,
            }
        )

    def to_state(self) -> dict[str, object]:  # @stub-protected
        """Serialize to the state document's root ``gate`` payload."""
        return {
            "step": self.step,
            "status": self.status.value,
            "consult_rounds": self.consult_rounds,
            "last_handoff": self.last_handoff,
        }

    @classmethod
    def from_state(  # @stub-protected
        cls, payload: Optional[Mapping[str, object]]
    ) -> "GateLock | None":
        """Build from a root ``gate`` payload; ``None``/empty yields ``None``.

        A legacy blob without the key (or with ``null``) has no lock, so it
        loads unchanged.
        """
        if not payload:
            return None
        raw_status = payload.get("status")
        try:
            status = (
                raw_status
                if isinstance(raw_status, GateStatus)
                else GateStatus(str(raw_status))
            )
        except ValueError:
            status = GateStatus.ARMED
        try:
            rounds = int(payload.get("consult_rounds") or 0)
        except (TypeError, ValueError):
            rounds = 0
        handoff = payload.get("last_handoff")
        return cls(
            step=str(payload.get("step") or ""),
            status=status,
            consult_rounds=max(rounds, 0),
            last_handoff=str(handoff) if handoff else None,
        )
