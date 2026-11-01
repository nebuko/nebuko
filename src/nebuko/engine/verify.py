"""Claim-verification loop for architect→context hand-offs.

Schema lives in :mod:`nebuko.engine.finalize` (:class:`VerificationClaim`,
``AgentCall.verify``); this module owns the engine-side rules that depend on
run state the model does not know: the per-tier claim cap, the minimum refute
ratio, rendering the claims into the callee task, and the per-run claim
ledger feeding the retrospective metrics.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from nebuko.engine.finalize import VerificationClaim

__all__ = [
    "CLAIM_CAPS",
    "MIN_REFUTE_RATIO",
    "VERDICT_VALUES",
    "ClaimLedger",
    "ClaimTaskRenderer",
    "ClaimValidationResult",
    "ClaimsValidator",
]

#: Maximum number of claims per complexity tier (enabled at every tier).
CLAIM_CAPS: dict[str, int] = {"low": 3, "medium": 5, "high": 8}

#: At least this share of the claims must be ``kind="refute"``.
MIN_REFUTE_RATIO: float = 1.0 / 3.0

#: The exact verdict vocabulary of the context report's CLAIM VERIFICATION table.
VERDICT_VALUES: tuple[str, ...] = ("confirmed", "refuted", "contested", "inconclusive")

_CLAIMS_HEADER = "VERIFY THESE CLAIMS (independently; report verdict per claim)"


class ClaimValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)


class ClaimsValidator:
    """Validate a ``verify`` payload against the engine's claim rules."""

    def validate(
        self, agent_name: str, claims: list[VerificationClaim], complexity: str
    ) -> ClaimValidationResult:
        # @stub-protected: signature defined by architect — do not modify
        raise NotImplementedError


class ClaimTaskRenderer:
    """Render claims into the callee task; never any draft-plan content."""

    def render(self, task: str, claims: list[VerificationClaim]) -> str:
        # @stub-protected: signature defined by architect — do not modify
        raise NotImplementedError


class ClaimLedger:
    """Per-run record of claims and verdicts, aggregated for the retrospective."""

    def record(
        self, run_id: str, claims: list[VerificationClaim], verdicts: dict[str, str]
    ) -> None:
        # @stub-protected: signature defined by architect — do not modify
        raise NotImplementedError

    def summarize(self) -> dict[str, Any]:
        # @stub-protected: signature defined by architect — do not modify
        raise NotImplementedError
