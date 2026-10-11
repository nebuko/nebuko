"""Boundary integration for the complexity governance subsystem (issue #882).

This module is the seam between the pure governor and the run pipeline:

* :func:`resolve_run_policy` merges the builtin layer with the repo layer read
  from the same agent-config source that carries ``complexity_overrides``.
* :func:`compute_boundary_adjustment` collects live evidence from run state and
  asks the governor for a drift adjustment at an invocation boundary.
* :func:`distribution_report` builds the retrospective calibration report.

Issue-linkage context is an intake-surface concern the plan flags as a call-site
confirmation: :func:`context_signals_from_env` reads an optional
``COMPLEXITY_CONTEXT_SIGNALS`` JSON blob the intake step may publish. Absent
that, linkage signals simply never fire (the mechanism is wired; the counts are
supplied by the caller).
"""

import json
import os
from typing import Optional

from config.sub_domains.complexity_policy.complexity_policy import (
    BUILTIN_LAYER,
    ComplexityPolicy,
    ComplexityPolicyLayer,
)

from .complexity_governor import (
    ComplexityAdjustment,
    ComplexityDistributionReport,
    ComplexityGovernor,
)
from .evidence import ContextSignals, collect_evidence, human_signals_from_state

#: Env var carrying the resolved policy snapshot into the agent run.
POLICY_ENV = "COMPLEXITY_POLICY"
#: Env var carrying the boundary adjustment into the agent run.
ADJUSTMENT_ENV = "COMPLEXITY_ADJUSTMENT"
#: Optional env var: JSON context signals published by the intake surface.
CONTEXT_SIGNALS_ENV = "COMPLEXITY_CONTEXT_SIGNALS"


def load_repo_policy_layer() -> Optional[ComplexityPolicyLayer]:
    """Read the optional repo policy layer from the agent config, best-effort.

    Returns ``None`` when the config cannot be read or declares no
    ``complexity_policy`` — a repo need not override the builtin rubric.
    """
    try:
        from config import load_config

        app = load_config(os.environ.get("AGENT_CONFIG_PATH") or "agent-config.yaml")
        return app.complexity_policy
    except Exception:
        return None


def resolve_run_policy(
    repo_layer: Optional[ComplexityPolicyLayer] = None,
) -> ComplexityPolicy:
    """Resolve the effective policy: builtin merged under any repo layer."""
    if repo_layer is None:
        repo_layer = load_repo_policy_layer()
    layers = [BUILTIN_LAYER]
    if repo_layer is not None:
        layers.append(repo_layer)
    return ComplexityPolicy.resolve(layers)


def context_signals_from_env() -> ContextSignals:
    """Read optional intake context signals from ``COMPLEXITY_CONTEXT_SIGNALS``."""
    raw = os.environ.get(CONTEXT_SIGNALS_ENV, "")
    if not raw:
        return ContextSignals()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ContextSignals()
    if not isinstance(data, dict):
        return ContextSignals()
    try:
        return ContextSignals.model_validate(data)
    except Exception:
        return ContextSignals()


def _latest_review_request(state, history: list) -> Optional[object]:
    """The most recent agent review request recorded in ``history`` (or None)."""
    for entry in reversed(history):
        request = getattr(entry, "complexity_review_request", None)
        if request is not None:
            return request
    return None


def compute_boundary_adjustment(
    state,
    policy: ComplexityPolicy,
    *,
    context: Optional[ContextSignals] = None,
) -> Optional[ComplexityAdjustment]:
    """Collect live evidence from ``state`` and evaluate a tier adjustment.

    An agent-issued review request recorded during the previous step (the tool
    path) is adjudicated in preference to the automatic drift check; absent a
    request, the governor's boundary drift check runs over the full evidence
    bundle.
    """
    history = list(getattr(state, "history", []) or [])
    evidence = collect_evidence(
        history,
        human=human_signals_from_state(state, history),
        context=context if context is not None else context_signals_from_env(),
    )
    governor = ComplexityGovernor()
    request = _latest_review_request(state, history)
    if request is not None:
        try:
            return governor.adjudicate(request, evidence, policy)
        except ValueError:
            # An invalid recorded request must never break routing; fall back
            # to the automatic drift check.
            pass
    return governor.evaluate_boundary(history, evidence, policy)


def distribution_report(
    state, repo: str, policy: ComplexityPolicy
) -> ComplexityDistributionReport:
    """Per-repo effective-tier calibration report for the retrospective step."""
    return ComplexityDistributionReport.from_history(
        list(getattr(state, "history", []) or []), policy, repo
    )
