"""Pinned-vs-summary history selection policy (issue #617).

Pure, side-effect-free policy that partitions an issue's comments into the set
to inject verbatim (explicitly pinned) and the set to inject as summaries
(everything else). Bounds the agent context by keeping only the high-signal,
curated comments in full instead of forwarding the whole thread.
"""

from dataclasses import dataclass
from typing import Optional

from workflow_state import WorkflowState


# @stub-protected
@dataclass(frozen=True)
class HistorySelection:
    """Immutable partition of comments into verbatim and summarized sets.

    ``verbatim`` comments are rendered with their full body; ``summarized``
    comments are rendered as a bot summary when available, otherwise a
    truncated excerpt.
    """

    verbatim: tuple[dict, ...]
    summarized: tuple[dict, ...]

    # @stub-protected
    @property
    def verbatim_ids(self) -> frozenset[int]:
        """Comment ids that must be rendered verbatim."""
        return frozenset(
            c.get("id") for c in self.verbatim if c.get("id") is not None
        )

    # @stub-protected
    @property
    def summarized_ids(self) -> frozenset[int]:
        """Comment ids that must be rendered as summaries/excerpts."""
        return frozenset(
            c.get("id") for c in self.summarized if c.get("id") is not None
        )


def _pinned_ids(state: Optional[WorkflowState]) -> set[int]:
    """Collect the ids of every explicitly pinned comment in ``state``.

    A comment is pinned when its id is listed in ``state.pinned_comment_ids``
    OR it appears in the ``comment_ids`` of a history entry with ``pin=True``.
    """
    if state is None:
        return set()

    ids = {cid for cid in state.pinned_comment_ids if cid is not None}
    for entry in state.history:
        if entry.pin:
            ids.update(cid for cid in entry.comment_ids if cid is not None)
    return ids


# @stub-protected
def select_history_comments(
    comments: list[dict],
    state: Optional[WorkflowState],
) -> HistorySelection:
    """Partition ``comments`` into verbatim (pinned) and summarized sets.

    A comment is verbatim iff its id is pinned. Every other comment is
    summarized. Order within each bucket follows the input order.
    """
    pinned = _pinned_ids(state)
    verbatim = tuple(c for c in comments if c.get("id") in pinned)
    summarized = tuple(c for c in comments if c.get("id") not in pinned)
    return HistorySelection(verbatim=verbatim, summarized=summarized)
