"""Factual turn-outcome domain entity (issue #923).

The single factual record of how one agent turn ended: the engine-measured
*completion* (``status``) plus the agent's *verdict* (``appreciation``). Every
routing decision is derived from this tuple, so navigation intent
(``advance``/``loop``/``terminate``) is removed from the agent's vocabulary.

Why the axes are shaped this way:

- ``status`` is **engine-derived**, never agent-declared: an INCOMPLETE turn
  (soft timeout, interrupt, crash) never gets to submit anything, and the
  engine already measures it via ``classify_run`` / ``OutcomeStatus``. On a
  conflict the engine observation wins; the agent's claim is preserved in
  ``note`` instead.
- ``appreciation`` is the agent's verdict and *is* the workflow ``on_result``
  key (``approved``/``rejected``/``hold``), which is what makes the
  ``default/review`` round-2 dead-end structurally impossible (there is no
  free-text ``reason='round N'`` or optional ``branch`` to lose in
  serialization).
- ``soft_kill`` is not an axis value: it is engine-emitted, so it becomes
  ``status=incomplete, incomplete_reason=soft_timeout``.

Fields are declared **without defaults** wherever they are mandatory for a
given state, so ``model_dump(exclude_defaults=True)`` can never silently drop
the outcome.
"""

from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:
    from ....agent_script.features.run_agent.run_outcome import OutcomeStatus


class TurnStatus(str, Enum):  # @stub-protected
    """Whether the agent turn completed or was cut short (engine-measured)."""

    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class Appreciation(str, Enum):  # @stub-protected
    """The agent's verdict on the turn's work; doubles as the ``on_result`` key."""

    APPROVED = "approved"
    REJECTED = "rejected"
    HOLD = "hold"


class IncompleteReason(str, Enum):  # @stub-protected
    """Why a turn did not complete (engine-measured)."""

    SOFT_TIMEOUT = "soft_timeout"
    INTERRUPTED = "interrupted"
    FAILURE = "failure"
    NO_OUTCOME = "no_outcome"
    AGENT_CHECKPOINT = "agent_checkpoint"


class HoldKind(str, Enum):  # @stub-protected
    """Which human the ``HOLD`` verdict is waiting on."""

    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"


# Engine ``OutcomeStatus`` value -> the INCOMPLETE reason it implies.
# ``success`` is absent: a successful run is COMPLETED (the agent supplies the
# appreciation), never an incomplete reason.
_OUTCOME_STATUS_TO_INCOMPLETE = {
    "gracefully_timed_out": IncompleteReason.SOFT_TIMEOUT,
    "interrupted": IncompleteReason.INTERRUPTED,
    "failure": IncompleteReason.FAILURE,
    "agent_checkpoint": IncompleteReason.AGENT_CHECKPOINT,
}


class TurnOutcome(BaseModel):
    """The factual record of how one agent turn ended.

    Attributes:
        status: Engine-measured completion. Mandatory, no default.
        appreciation: The agent's verdict; mandatory iff
            ``status == completed``.
        incomplete_reason: Why the turn did not complete; mandatory iff
            ``status == incomplete``.
        hold_kind: Which human a ``HOLD`` appreciation awaits; mandatory iff
            ``appreciation == hold``.
        required_agent: Optional hand-off target for a callee return
            (formalizes the function-call callback, issues #765/#876).
        round: dev<->review cycle counter; replaces the free-text "round N".
        note: Human-facing explanation only — never routed on.
    """

    model_config = ConfigDict(frozen=True)

    status: TurnStatus
    appreciation: Optional[Appreciation] = None
    incomplete_reason: Optional[IncompleteReason] = None
    hold_kind: Optional[HoldKind] = None
    required_agent: Optional[str] = None
    round: int = 0
    note: str = ""

    @model_validator(mode="after")
    def _validate_axes(self) -> "TurnOutcome":
        """Enforce the cross-field iff rules that make the grid reachable-only."""
        completed = self.status == TurnStatus.COMPLETED
        if completed and self.appreciation is None:
            raise ValueError("appreciation is required when status == completed")
        if not completed and self.appreciation is not None:
            raise ValueError("appreciation is forbidden when status == incomplete")
        if completed and self.incomplete_reason is not None:
            raise ValueError("incomplete_reason is forbidden when status == completed")
        if not completed and self.incomplete_reason is None:
            raise ValueError("incomplete_reason is required when status == incomplete")
        if self.hold_kind is not None and self.appreciation != Appreciation.HOLD:
            raise ValueError("hold_kind is only valid when appreciation == hold")
        if self.appreciation == Appreciation.HOLD and self.hold_kind is None:
            raise ValueError("hold_kind is required when appreciation == hold")
        return self

    @property
    def route_key(self) -> Optional[str]:  # @stub-protected
        """The workflow ``on_result`` key this outcome routes on, or ``None``.

        ``None`` for an INCOMPLETE turn: an incomplete turn never routes on a
        verdict, it is retried or recalled instead.
        """
        return self.appreciation.value if self.appreciation is not None else None

    @classmethod
    def from_agent_payload(  # @stub-protected
        cls,
        payload: Mapping[str, Any],
        *,
        observed: "OutcomeStatus",
    ) -> "TurnOutcome":
        """Build from the agent's payload, with the engine observation winning.

        The engine-measured ``observed`` status is authoritative: when it says
        the turn did not complete, the result is INCOMPLETE regardless of what
        the agent claimed, and any conflicting appreciation is preserved in
        ``note`` (never in ``appreciation``).

        Args:
            payload: Agent-supplied fields (``appreciation``, ``hold_kind``,
                ``required_agent``, ``round``, ``note``). When the key is
                present with a value it must be a valid appreciation; an absent
                key or an explicit ``None`` means "no verdict".
            observed: The engine's ``OutcomeStatus`` for this turn.

        Returns:
            A validated :class:`TurnOutcome`.

        Raises:
            ValueError: The payload states an appreciation that is present but
                is not a valid verdict (a typo or a blank string).
        """
        observed_value = getattr(observed, "value", str(observed))
        incomplete_reason = _OUTCOME_STATUS_TO_INCOMPLETE.get(observed_value)
        note = _coerce_note(payload.get("note"))
        required_agent = _coerce_agent(payload.get("required_agent"))
        # A payload that carries the appreciation key must carry a usable
        # verdict. Defaulting a misspelling *or a blank string* to "approved"
        # would advance a step the agent never approved (issue #964) — fail
        # loudly instead. Only an explicit ``None`` (or an absent key) stays
        # "no verdict": the engine's own observed-status call sites (soft kill,
        # crash, no action) build hand-made payloads that name no appreciation
        # at all.
        has_appreciation = "appreciation" in payload
        raw_appreciation = payload.get("appreciation")
        appreciation = _coerce_appreciation(raw_appreciation)
        if appreciation is None and has_appreciation and raw_appreciation is not None:
            raise ValueError(
                f"invalid appreciation {raw_appreciation!r}: expected "
                f"one of {[a.value for a in Appreciation]}"
            )
        if incomplete_reason is not None:
            if appreciation is not None:
                note = (
                    f"(agent claimed appreciation={appreciation.value}; "
                    f"engine observed {observed_value}) {note}".strip()
                )
            return cls.for_incomplete(incomplete_reason, note=note)
        if appreciation is None:
            # Only reachable for a payload without the key (an internal call
            # site); the tool itself always sends it.
            appreciation = Appreciation.APPROVED
        hold_kind = _coerce_hold_kind(payload.get("hold_kind"))
        if appreciation == Appreciation.HOLD and hold_kind is None:
            hold_kind = HoldKind.AWAITING_INPUT
        return cls(
            status=TurnStatus.COMPLETED,
            appreciation=appreciation,
            hold_kind=hold_kind if appreciation == Appreciation.HOLD else None,
            required_agent=required_agent,
            round=_coerce_round(payload.get("round")),
            note=note,
        )

    @classmethod
    def for_incomplete(  # @stub-protected
        cls, reason: IncompleteReason, *, note: str = ""
    ) -> "TurnOutcome":
        """Build an INCOMPLETE outcome for an engine-observed reason."""
        return cls(status=TurnStatus.INCOMPLETE, incomplete_reason=reason, note=note)


def _coerce_appreciation(value: Any) -> Optional[Appreciation]:
    if isinstance(value, Appreciation):
        return value
    if value in (None, ""):
        return None
    try:
        return Appreciation(str(value))
    except ValueError:
        return None


def _coerce_hold_kind(value: Any) -> Optional[HoldKind]:
    if isinstance(value, HoldKind):
        return value
    if value in (None, ""):
        return None
    try:
        return HoldKind(str(value))
    except ValueError:
        return None


def _coerce_note(value: Any) -> str:
    return str(value) if value not in (None, "") else ""


def _coerce_agent(value: Any) -> Optional[str]:
    text = str(value).strip() if value not in (None, "") else ""
    return text or None


def _coerce_round(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "TurnStatus",
    "Appreciation",
    "IncompleteReason",
    "HoldKind",
    "TurnOutcome",
]
