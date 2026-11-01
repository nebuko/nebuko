"""Verify-before-commit: falsifiable claims attached to architect hand-offs.

Issue #1250: when @@architect hands off to @@context it lists the external
facts its plan depends on as :class:`VerificationClaim` objects, so the
research step can confirm or refute each one independently of the draft plan.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class VerificationClaim(BaseModel):
    """One falsifiable external fact the plan depends on."""

    id: str
    claim: str
    kind: Literal["support", "refute"]
    category: Literal[
        "build_vs_buy",
        "version",
        "capability",
        "limits_pricing",
        "alternatives",
        "compatibility",
    ]
    if_wrong: str = Field(min_length=10)


class ClaimsValidator:
    """Validate the ``verify`` payload of an architect-to-context hand-off."""

    CLAIM_CAPS: dict[str, int] = {"low": 3, "medium": 5, "high": 8}
    MIN_REFUTE_RATIO: float = 1.0 / 3.0

    def validate(
        self,
        claims: list[VerificationClaim],
        *,
        agent_name: str,
        complexity: str = "medium",
    ) -> Optional[str]:
        """Return an agent-facing error message, or ``None`` when valid."""
        if agent_name != "context":
            return "verify is only valid on a hand-off to context"
        if len(claims) > self.CLAIM_CAPS[complexity]:
            return (
                f"too many claims for complexity '{complexity}': {len(claims)} "
                f"(cap {self.CLAIM_CAPS[complexity]})"
            )
        ids = [claim.id for claim in claims]
        if len(ids) != len(set(ids)):
            return "duplicate claim ids"
        refute = sum(1 for claim in claims if claim.kind == "refute")
        if claims and refute / len(claims) < self.MIN_REFUTE_RATIO:
            return "at least one third of the claims must be kind='refute'"
        return None


class ClaimTaskRenderer:
    """Render the claims into the callee's task as a fixed evidence table."""

    def render(self, claims: list[VerificationClaim]) -> str:
        lines = [
            "### CLAIM VERIFICATION",
            "",
            "| ID | Verdict | Evidence (URL, fetched date) | Verbatim excerpt |",
            "|---|---|---|---|",
        ]
        for claim in claims:
            lines.append(f"| {claim.id} | | | |")
        return "\n".join(lines)
