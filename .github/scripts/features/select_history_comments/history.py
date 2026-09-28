"""History window selection policy for agent invocation (issue #683).

Bounds the number of prior issue comments injected into an agent call: the
last ``history_comment_count`` comments (default ``5``) plus every pinned
comment. Pinned comments are always injected and never counted against the
window.

This pure policy lives alongside the existing pinning policy in the
history-selection feature package, following the repo's feature-module
convention (the plan's ``openhands_multi_agents/history.py`` path is adapted
to the actual package path).

Stub step: class/function skeletons only. Bodies are materialised by @@dev in
the implement step.
"""

from dataclasses import dataclass
from typing import Any, Collection, Sequence


# A GitHub issue comment, as returned by the API (dict payload).
Comment = dict[str, Any]

DEFAULT_HISTORY_COMMENT_COUNT = 5


# @stub-protected
@dataclass(frozen=True)
class HistoryWindowConfig:
    """Immutable carrier of the history-window parameters for one invocation."""

    history_comment_count: int = DEFAULT_HISTORY_COMMENT_COUNT  # @stub-protected

    # @stub-protected
    def normalized_count(self) -> int:
        """Clamp to a non-negative int; treat None/invalid as the default 5."""
        raise NotImplementedError


# @stub-protected
@dataclass(frozen=True)
class HistorySelection:
    """Immutable result holding the exact comment set injected into an agent call."""

    pinned_comments: tuple[Comment, ...]  # @stub-protected — always injected
    recent_comments: tuple[Comment, ...]  # @stub-protected — last N, chronological
    requested_count: int  # @stub-protected — the resolved N

    # @stub-protected
    def all_comments(self) -> tuple[Comment, ...]:
        """Pinned first (stable), then recent, de-duplicated by id, chronological output."""
        raise NotImplementedError


# @stub-protected
def select_history(
    comments: Sequence[Comment],
    pinned_ids: Collection[str],
    config: HistoryWindowConfig,
) -> HistorySelection:
    """Take last ``config.normalized_count()`` comments (excluding pinned), plus all pinned."""
    raise NotImplementedError
