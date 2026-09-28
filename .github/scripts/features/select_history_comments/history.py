"""History window selection policy for agent invocation (issue #683).

Bounds the number of prior issue comments injected into an agent call: the last
``history_comment_count`` comments (default ``5``) of the thread, unioned with
every pinned comment. A pinned comment that also falls inside the window is
injected once.

The window is the last ``N`` comments overall (``N = normalized_count()``);
pinned comments that fall outside the window are added on top, and pinned
comments inside the window are de-duplicated. The output is a single
chronological (ascending) order, de-duplicated by id.

Edge cases:

- value omitted / ``None`` / non-numeric -> default ``5``
- negative value -> default ``5`` (not clamped to ``0``)
- ``0`` -> pinned-only
- more pinned than ``N`` -> every pinned comment is still injected
- fewer comments than ``N`` -> inject all available

Accepted input domain: ``history_comment_count`` may arrive as ``int`` (direct
use), ``str`` (a GitHub workflow input is always a string), or ``None``
(omitted) — normalization is a real validation boundary, not an accident.

``DEFAULT_HISTORY_COMMENT_COUNT`` must stay equal to the ``history_comment_count``
default declared in ``.github/workflows/openhands-multi-agent.yml``; the default
is necessarily duplicated across the two languages.

This pure policy lives alongside the existing pinning policy in the
history-selection feature package, following the repo's feature-module
convention (the plan's ``openhands_multi_agents/history.py`` path is adapted
to the actual package path).
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

    history_comment_count: int | str | None = DEFAULT_HISTORY_COMMENT_COUNT  # @stub-protected

    # @stub-protected
    def normalized_count(self) -> int:
        """Normalize to a non-negative int.

        Numeric strings are parsed, ``0`` is honored, and negative,
        non-numeric, or ``None`` inputs fall back to
        ``DEFAULT_HISTORY_COMMENT_COUNT``.
        """
        raw = self.history_comment_count
        if isinstance(raw, bool):  # bool is an int subclass; not a count
            return DEFAULT_HISTORY_COMMENT_COUNT
        if isinstance(raw, int):
            return raw if raw >= 0 else DEFAULT_HISTORY_COMMENT_COUNT
        if raw is None:
            return DEFAULT_HISTORY_COMMENT_COUNT
        try:
            parsed = int(str(raw).strip())
        except (TypeError, ValueError):
            return DEFAULT_HISTORY_COMMENT_COUNT
        return parsed if parsed >= 0 else DEFAULT_HISTORY_COMMENT_COUNT


def _deduplicate_by_id(comments: list[Comment]) -> list[Comment]:
    """Drop later duplicates sharing a non-``None`` id, keeping first occurrence."""
    seen: set[str] = set()
    result: list[Comment] = []
    for comment in comments:
        cid = comment.get("id")
        if cid is not None:
            key = str(cid)
            if key in seen:
                continue
            seen.add(key)
        result.append(comment)
    return result


def _chronological_key(comment: Comment) -> tuple[int, int, str]:
    """Order comments by id; GitHub ids increase with creation time.

    Comments carrying a non-integer/absent id sort after integer-id comments,
    preserving their relative insertion order (Python's sort is stable).
    """
    cid = comment.get("id")
    if isinstance(cid, int):
        return (0, cid, "")
    return (1, 0, "" if cid is None else str(cid))


# @stub-protected
@dataclass(frozen=True)
class HistorySelection:
    """Immutable result holding the exact comment set injected into an agent call."""

    pinned_comments: tuple[Comment, ...]  # @stub-protected — always injected
    recent_comments: tuple[Comment, ...]  # @stub-protected — last N, chronological
    requested_count: int  # @stub-protected — the resolved N

    # @stub-protected
    def all_comments(self) -> tuple[Comment, ...]:
        """Last N overall + pinned, de-duplicated by id, chronological ascending."""
        merged = _deduplicate_by_id(
            list(self.pinned_comments) + list(self.recent_comments)
        )
        return tuple(sorted(merged, key=_chronological_key))


# @stub-protected
def select_history(
    comments: Sequence[Comment],
    pinned_ids: Collection[str],
    config: HistoryWindowConfig,
) -> HistorySelection:
    """Take the last ``config.normalized_count()`` comments overall, plus all pinned."""
    count = config.normalized_count()
    pinned_keys = {str(pid) for pid in pinned_ids}
    pinned_comments = [c for c in comments if str(c.get("id")) in pinned_keys]
    recent_comments = list(comments[-count:]) if count > 0 else []
    return HistorySelection(
        pinned_comments=tuple(pinned_comments),
        recent_comments=tuple(recent_comments),
        requested_count=count,
    )
