"""Typed evidence bundled for complexity adjudication.

An :class:`EvidenceBundle` fuses three evidence families the governor weighs
together:

* ``llm`` — the observed metric vocabulary, reusing :mod:`metrics_schema` field
  names (an open ``dict[str, float]``).
* ``human`` — interaction signals: feedback rounds, change-requests, approvals.
* ``context`` — intake context: issue linkage counts and reopen state.

Pydantic models so the bundle can be embedded in the ``complexity_review``
tool input *and* in policy config envelopes. Deliberately SDK-free: this module
is imported by ``workflow_state``, which must stay importable without the agent
runtime (see pyproject ``runtime`` optional-dependencies).
"""

from typing import Optional

from pydantic import BaseModel, Field

#: StateMetrics fields carried into the LLM evidence family.
_LLM_METRIC_FIELDS = (
    "input_tokens",
    "output_tokens",
    "nb_tool_calls",
    "cost",
    "tool_call_error_rate",
    "review_fix_requests",
    "dispatch_recall_count",
)


class HumanSignals(BaseModel):
    """Human interaction signals observed over the run's history."""

    feedback_rounds: int = 0  # @stub-protected
    changes_requested: int = 0  # @stub-protected
    approvals: int = 0  # @stub-protected


class ContextSignals(BaseModel):
    """Intake context signals (issue cross-references)."""

    linked_issues: int = 0  # @stub-protected
    linked_recent_issues: int = 0  # @stub-protected
    reopened: bool = False  # @stub-protected


class EvidenceBundle(BaseModel):
    """The fused evidence an adjudication decision is justified against."""

    llm: dict[str, float] = Field(default_factory=dict)  # @stub-protected
    human: HumanSignals = Field(default_factory=HumanSignals)  # @stub-protected
    context: ContextSignals = Field(default_factory=ContextSignals)  # @stub-protected


def is_empty_evidence(bundle: EvidenceBundle) -> bool:
    """True when no evidence family carries any signal.

    A review request with an empty bundle is rejected: a tier change must be
    justified by something.
    """
    if bundle.llm:
        return False
    human = bundle.human
    if human.feedback_rounds or human.changes_requested or human.approvals:
        return False
    context = bundle.context
    if context.linked_issues or context.linked_recent_issues or context.reopened:
        return False
    return True


def llm_evidence_from_metrics(metrics) -> dict[str, float]:
    """Extract the numeric evidence fields from a ``StateMetrics`` record."""
    if metrics is None:
        return {}
    values: dict[str, float] = {}
    for name in _LLM_METRIC_FIELDS:
        raw = getattr(metrics, name, None)
        if isinstance(raw, bool) or raw is None:
            continue
        if isinstance(raw, (int, float)):
            values[name] = float(raw)
    return values


def human_signals_from_state(state, history=None) -> HumanSignals:
    """Derive :class:`HumanSignals` from a run state's recorded human events.

    ``changes_requested`` sums the per-step ``review_fix_requests`` counters,
    ``approvals`` counts approved gate decisions, and ``feedback_rounds`` counts
    every recorded gate decision (each gate interaction is one human round).
    """
    entries = list(history if history is not None else getattr(state, "history", []) or [])
    changes = 0
    for entry in entries:
        metrics = getattr(entry, "metrics", None)
        changes += int(getattr(metrics, "review_fix_requests", 0) or 0)

    approvals = 0
    rounds = 0
    for decision in getattr(state, "gate_history", []) or []:
        rounds += 1
        if str(getattr(decision, "status", "")).lower() == "approved":
            approvals += 1
    return HumanSignals(
        feedback_rounds=rounds, changes_requested=changes, approvals=approvals
    )


def collect_evidence(
    history,
    *,
    human: Optional[HumanSignals] = None,
    context: Optional[ContextSignals] = None,
    extra_llm: Optional[dict[str, float]] = None,
) -> EvidenceBundle:
    """Build an :class:`EvidenceBundle` from history plus live signals.

    LLM evidence comes from the most recent history entry carrying metrics
    (merged under any ``extra_llm`` the caller supplies); human and context
    signals are passed in by the caller's history-write boundary.
    """
    llm: dict[str, float] = {}
    for entry in reversed(list(history or [])):
        metrics = getattr(entry, "metrics", None)
        if metrics is not None:
            llm = llm_evidence_from_metrics(metrics)
            break
    if extra_llm:
        llm.update(extra_llm)
    return EvidenceBundle(
        llm=llm,
        human=human or HumanSignals(),
        context=context or ContextSignals(),
    )
