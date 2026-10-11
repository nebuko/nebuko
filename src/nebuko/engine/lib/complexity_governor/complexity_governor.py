"""The single complexity decision-maker for a run.

``ComplexityGovernor`` turns collected evidence and agent review requests into
clamped tier adjustments under a repo-resolved :class:`ComplexityPolicy`
(``effective = clamp(base + delta)``). It is deliberately stateless: policy and
evidence are passed per call, so every decision is pure, replayable, and unit
testable. Decisions are recorded on :class:`ComplexityAdjustment` — an immutable
record persisted to workflow state.

The canonical :class:`ComplexityReviewRequest` input model lives here (not in
the tool feature) on purpose: ``workflow_state`` persists review requests and
the engine library must import without the optional agent runtime
(``openhands-sdk``). The ``complexity_review`` tool imports this model and wraps
it in its SDK ``Action``.
"""

import json
from dataclasses import dataclass
from typing import Literal, Optional

from config.sub_domains.complexity_policy.complexity_policy import (
    TIERS,
    ComplexityPolicy,
    Tier,
)
from pydantic import BaseModel

from .evidence import EvidenceBundle, is_empty_evidence

Trigger = Literal["evidence-drift", "agent-request"]


def tier_index(tier: str) -> int:
    """Index of ``tier`` in the ordered ``low < medium < high`` ladder."""
    try:
        return TIERS.index(tier)
    except ValueError:
        return TIERS.index("medium")


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass(frozen=True)
class ComplexityAdjustment:
    """Immutable record of one complexity decision, persisted to run state."""

    delta: int  # @stub-protected
    trigger: Trigger  # @stub-protected
    evidence: EvidenceBundle  # @stub-protected
    feeling: str = ""  # @stub-protected
    rationale: str = ""  # @stub-protected

    def to_json(self) -> str:
        """Serialize for the state/pin channel (env var + comment payload)."""
        return json.dumps(
            {
                "delta": self.delta,
                "trigger": self.trigger,
                "evidence": self.evidence.model_dump(),
                "feeling": self.feeling,
                "rationale": self.rationale,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> Optional["ComplexityAdjustment"]:
        """Parse a serialized adjustment; ``None`` for blank/invalid input."""
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return cls(
            delta=int(data.get("delta", 0)),
            trigger=data.get("trigger", "evidence-drift"),
            evidence=EvidenceBundle.model_validate(data.get("evidence") or {}),
            feeling=data.get("feeling", ""),
            rationale=data.get("rationale", ""),
        )


@dataclass(frozen=True)
class ComplexityDistributionReport:
    """Per-repo calibration report comparing actual tiers to the target mix."""

    repo: str  # @stub-protected
    counts: dict[str, int]  # @stub-protected
    target: dict[str, float]  # @stub-protected
    delta_from_target: dict[str, float]  # @stub-protected

    @classmethod
    def from_history(
        cls, history, policy: ComplexityPolicy, repo: str
    ) -> "ComplexityDistributionReport":  # @stub-protected
        """Count effective tiers across ``history`` against ``policy.target_distribution``."""
        counts = {tier: 0 for tier in TIERS}
        for entry in history or []:
            tier = _effective_tier(entry)
            if tier is not None:
                counts[tier] += 1

        total = sum(counts.values())
        target = {tier: float(policy.target_distribution.get(tier, 0.0)) for tier in TIERS}
        delta = {
            tier: (counts[tier] / total if total else 0.0) - target[tier]
            for tier in TIERS
        }
        return cls(repo=repo, counts=counts, target=target, delta_from_target=delta)


def _effective_tier(entry) -> Optional[Tier]:
    """The effective tier a history entry ran at, or ``None`` when unknown."""
    metrics = getattr(entry, "metrics", None)
    complexity = getattr(metrics, "complexity", None)
    if complexity in TIERS:
        return complexity
    return None


class ComplexityReviewRequest(BaseModel):
    """Canonical agent review request adjudicated by the governor."""

    evidence: EvidenceBundle  # @stub-protected
    feeling: str  # @stub-protected
    suggested_tier: Optional[Tier] = None  # @stub-protected  advisory input only


class ComplexityGovernor:
    """Stateless adjudicator of complexity adjustments."""

    def evaluate_boundary(
        self, history, live_evidence: EvidenceBundle, policy: ComplexityPolicy
    ) -> Optional[ComplexityAdjustment]:  # @stub-protected
        """Automatic drift check over all evidence families at a boundary.

        Returns ``None`` when auto-adjust is disabled or the evidence does not
        move the tier. Otherwise the delta is clamped to ``policy.max_auto_delta``.
        """
        if not policy.auto_adjust:
            return None
        base = self._base_tier(history, policy)
        target = policy.classify(live_evidence)
        delta = _clamp(
            tier_index(target) - tier_index(base),
            -policy.max_auto_delta,
            policy.max_auto_delta,
        )
        if delta == 0:
            return None
        return ComplexityAdjustment(
            delta=delta,
            trigger="evidence-drift",
            evidence=live_evidence,
            feeling="",
            rationale=f"evidence drift {base} -> {target}",
        )

    def adjudicate(
        self,
        request: ComplexityReviewRequest,
        live_evidence: EvidenceBundle,
        policy: ComplexityPolicy,
    ) -> ComplexityAdjustment:  # @stub-protected
        """Adjudicate an agent review request (the tool path).

        Both evidence families are required: an empty bundle or a blank
        ``feeling`` is rejected, so a tier change always carries a justification.
        ``suggested_tier`` is advisory — the governor's own classification,
        bounded by ``policy.max_auto_delta``, decides the delta.
        """
        if is_empty_evidence(request.evidence):
            raise ValueError("complexity review requires evidence (at least one family)")
        if not (request.feeling or "").strip():
            raise ValueError("complexity review requires a non-blank feeling")

        base = policy.classify(live_evidence)
        target = request.suggested_tier or policy.classify(request.evidence)
        delta = _clamp(
            tier_index(target) - tier_index(base),
            -policy.max_auto_delta,
            policy.max_auto_delta,
        )
        return ComplexityAdjustment(
            delta=delta,
            trigger="agent-request",
            evidence=request.evidence,
            feeling=request.feeling.strip(),
            rationale=f"agent review {base} -> {target}"
            + (f" (suggested {request.suggested_tier})" if request.suggested_tier else ""),
        )

    @staticmethod
    def apply(
        base_tier: Tier, adjustment: Optional[ComplexityAdjustment]
    ) -> Tier:  # @stub-protected
        """Apply an adjustment to a base tier; result never escapes ``[low, high]``."""
        if adjustment is None:
            return base_tier
        index = _clamp(tier_index(base_tier) + adjustment.delta, 0, len(TIERS) - 1)
        return TIERS[index]

    @staticmethod
    def _base_tier(history, policy: ComplexityPolicy) -> Tier:
        """The tier the run most recently ran at (fallback ``policy.default_tier``)."""
        for entry in reversed(list(history or [])):
            tier = _effective_tier(entry)
            if tier is not None:
                return tier
        return policy.default_tier
