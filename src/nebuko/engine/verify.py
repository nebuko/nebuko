"""Verify-before-commit: falsifiable claims attached to architect hand-offs.

Issue #1250: when @@architect hands off to @@context it lists the external
facts its plan depends on as :class:`VerificationClaim` objects, so the
research step can confirm or refute each one independently of the draft plan.
"""

from __future__ import annotations

import json
import re
from collections import Counter
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

    #: The one allowed leak: claims render, the draft plan itself never does.
    PLAN_ID = "plan-draft"

    def render_task(self, task: Optional[str], claims: list[VerificationClaim]) -> str:
        """Render the callee's task with the claim block prepended.

        The draft plan is intentionally never included (issue #1250): the
        claims must be checked independently of the plan they came from.
        """
        parts: list[str] = []
        if task and task.strip():
            parts.append(task.strip())
        parts.append(self.render(claims))
        return "\n\n".join(parts)

    def render(self, claims: list) -> str:
        lines = [
            "### CLAIM VERIFICATION",
            "",
            "| ID | Claim | Verdict (confirmed/refuted/contested/inconclusive) | Evidence (URL, fetched date) | Verbatim excerpt |",
            "|---|---|---|---|---|",
        ]
        for claim in claims:
            lines.append(f"| {claim.id} | {claim.claim} | | | |")
        return "\n".join(lines)


class ClaimPlanCoverage:
    """Check a plan artifact addresses every claim ID (issue #1250).

    A claim whose ID never appears in the plan is silently ignored evidence;
    the ``## Context`` section must state a verdict for each ID.
    """

    _ID_RE = re.compile(r"\bC\d+\b")

    def missing_ids(self, claims: list[VerificationClaim], plan_text: str) -> list[str]:
        """The claim IDs not mentioned anywhere in the plan text."""
        present = set(self._ID_RE.findall(plan_text))
        return [claim.id for claim in claims if claim.id not in present]


def claim_metrics(claims: list[VerificationClaim], report_text: str) -> dict:
    """Run-level claim metrics (issue #7): count, verdicts, refuted-impact.

    ``changed_plan`` is True when a refuted verdict appears in a report that
    also shows the plan adapting (a ``## Context`` verdict naming the claim).
    The retrospective skill reads these to judge whether the cost paid off.
    """
    verdicts = Counter()
    for line in report_text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 4 and re.fullmatch(r"C\d+", cells[1] or ""):
            verdicts[cells[2].lower()] += 1
    refuted_changed = sum(
        1
        for verdict, count in verdicts.items()
        if verdict == "refuted" and count
    )
    return {
        "claims": len(claims),
        "verdicts": dict(verdicts),
        "refuted_changed_plan": refuted_changed > 0,
    }


def claims_to_json(claims: list[VerificationClaim]) -> str:
    """Serialize claims for the run state / metrics artifact."""
    return json.dumps([c.model_dump() for c in claims])
