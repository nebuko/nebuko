"""Complexity governance subsystem (issue #869).

Layered, repo-tunable complexity policy + a single stateless adjudicator
(:class:`ComplexityGovernor`) that turns LLM, human, and context evidence into
clamped tier adjustments.
"""

from .complexity_governor import (
    ComplexityAdjustment,
    ComplexityDistributionReport,
    ComplexityGovernor,
    ComplexityReviewRequest,
    tier_index,
)
from .evidence import (
    ContextSignals,
    EvidenceBundle,
    HumanSignals,
    collect_evidence,
    human_signals_from_state,
    is_empty_evidence,
)
from .integration import (
    ADJUSTMENT_ENV,
    POLICY_ENV,
    compute_boundary_adjustment,
    distribution_report,
    load_repo_policy_layer,
    resolve_run_policy,
)

__all__ = [
    "ComplexityAdjustment",
    "ComplexityDistributionReport",
    "ComplexityGovernor",
    "ComplexityReviewRequest",
    "tier_index",
    "ContextSignals",
    "EvidenceBundle",
    "HumanSignals",
    "collect_evidence",
    "human_signals_from_state",
    "is_empty_evidence",
    "ADJUSTMENT_ENV",
    "POLICY_ENV",
    "compute_boundary_adjustment",
    "distribution_report",
    "load_repo_policy_layer",
    "resolve_run_policy",
]
