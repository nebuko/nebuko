"""Test-side turn-outcome factories (issue #964).

Since the factual migration a ``HistoryEntry`` must record the turn outcome
that produced it, and a ``WorkflowState`` carries the latest one. Fixtures that
only care about *position* can use these neutral facts instead of spelling the
enums out at every call site.
"""

from __future__ import annotations

from workflow_state import Appreciation, IncompleteReason, TurnOutcome, TurnStatus


def approved_outcome(**overrides) -> TurnOutcome:
    """A completed turn whose agent verdict was ``approved``."""
    return TurnOutcome(
        status=TurnStatus.COMPLETED,
        appreciation=Appreciation.APPROVED,
        **overrides,
    )


def incomplete_outcome(reason: IncompleteReason = IncompleteReason.NO_OUTCOME, **overrides) -> TurnOutcome:
    """An engine-measured turn that carried no agent verdict."""
    return TurnOutcome.for_incomplete(reason, **overrides)
