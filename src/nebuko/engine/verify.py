"""Verify-before-commit: typed claims carried on the architect→context hand-off.

Issue #1250. The architect attaches falsifiable external-fact claims to its
``finalize`` hand-off; the engine validates them and renders them into the
callee's task so @@context checks each one independently of the draft plan.
"""

from typing import Literal

from pydantic import BaseModel, Field


class VerificationClaim(BaseModel):
    """One falsifiable external fact the plan depends on."""

    id: str  # "C1", "C2", …
    claim: str  # phrased so evidence can confirm OR refute it
    kind: Literal["support", "refute"]
    category: Literal[
        "build_vs_buy",
        "version",
        "capability",
        "limits_pricing",
        "alternatives",
        "compatibility",
    ]
    if_wrong: str = Field(min_length=10)  # what changes in the plan if false


class ClaimValidationResult(BaseModel):
    """Outcome of validating a hand-off's claim list."""

    ok: bool
    errors: list[str] = Field(default_factory=list)


# @stub-protected: signature defined by architect — do not modify
class ClaimsValidator:
    """Validate claims: context-only callee, caps per complexity, refute ratio."""

    #: Maximum number of claims allowed per complexity tier.
    CLAIM_CAPS: dict[str, int] = {"low": 3, "medium": 5, "high": 8}

    #: Minimum share of claims that must be ``kind="refute"``.
    MIN_REFUTE_RATIO: float = 1.0 / 3.0

    def validate(
        self,
        agent_name: str,
        claims: list[VerificationClaim],
        complexity: str,
    ) -> ClaimValidationResult:
        """Reject a non-context callee; enforce caps, ID uniqueness, refute ratio."""
        raise NotImplementedError


# @stub-protected: signature defined by architect — do not modify
class ClaimTaskRenderer:
    """Render claims into a fixed block appended to the callee's task.

    Renders claims ONLY; never receives or embeds draft-plan content.
    """

    HEADER = "VERIFY THESE CLAIMS (independently; report verdict per claim)"

    def render(self, task: str, claims: list[VerificationClaim]) -> str:
        """Append the fixed claim block to ``task``; claims only, never plan text."""
        raise NotImplementedError


# @stub-protected: signature defined by architect — do not modify
class ClaimLedger:
    """Per-run record of claims and verdicts, feeding the run metrics."""

    def __init__(self) -> None:
        self._records: list[dict] = []

    def record(
        self,
        run_id: str,
        claims: list[VerificationClaim],
        verdicts: dict[str, str],
    ) -> None:
        """Record one run's claims and their verdicts."""
        raise NotImplementedError

    def summarize(self) -> dict:
        """Aggregate the ledger: claims per run and verdict distribution."""
        raise NotImplementedError
