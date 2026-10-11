#!/usr/bin/env python3
"""Per-run bounded view of an issue's comment thread (issue #805, Pillar C).

A single engine run reads the same issue thread several times — prompt context,
workflow-state recovery, resolver fallbacks. :class:`ThreadSnapshot` is one
*bounded* read that carries both the comments and the latest workflow state, so
callers share a fetch instead of re-paginating the whole history each time.

The read is tail-bounded: :func:`load_thread_snapshot` delegates to
``github_client.fetch_issue_comments`` which jumps straight to the last page, so
its cost is a constant number of REST requests regardless of thread length.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence, Tuple

from github_client import fetch_issue_comments
from workflow_state import extract_latest_state_from_comments

if TYPE_CHECKING:  # pragma: no cover - typing only
    from github_client import GitHubTransport
    from workflow_state.workflow_state import WorkflowState

__all__ = [
    "ThreadSnapshot",
    "STATE_TAIL_FLOOR",
    "build_comment_index",
    "build_snapshot",
    "load_thread_snapshot",
    "state_tail_limit",
]

HISTORY_COMMENT_COUNT_ENV = "HISTORY_COMMENT_COUNT"

# A state lookup must never miss the newest state comment, which is posted at
# the end of the previous run. Reserving a fixed tail keeps that read to a
# single page even when the prompt-context limit is set very low.
STATE_TAIL_FLOOR = 50


@dataclass(frozen=True)
class ThreadSnapshot:
    """One bounded read of an issue's comment thread.

    ``fetch_error`` records a failed read so it can never be mistaken for a
    legitimately empty thread (issue #805): callers can tell "no state yet"
    apart from "could not read the thread".
    """

    comments: Tuple[dict, ...] = ()
    state: Optional["WorkflowState"] = None
    fetched_at: float = 0.0
    fetch_error: Optional[str] = None


def _state_view(comments: Sequence[Mapping[str, Any]]) -> list:
    """Project raw comment dicts to the shape state extraction reads.

    The ``id`` rides along so the latest state can backfill its step-output
    locators with the comment ids that carried each step (issue #756).
    """
    return [
        {"body": (comment.get("body") or ""), "id": comment.get("id")}
        for comment in comments
    ]


def build_comment_index(
    comments: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Map comment id -> agent-facing body for step-output resolution.

    The body is stripped of its workflow-state block and bot routing markers,
    delegating to ``to_git_comment.strip_state_section`` so an output value is
    byte-identical to what ``_agent_facing_body`` shows agents. Both the raw
    ``id`` and the ``html_url`` (when present) key the same body, because the
    GitHub adapter's ``post`` returns a URL while raw comments carry a numeric
    id (issue #756).
    """
    from workflow_state.features.comment.to_git_comment import strip_state_section

    index: dict[str, str] = {}
    for comment in comments:
        identifier = comment.get("id")
        if identifier is None:
            continue
        body = strip_state_section(comment.get("body") or "")
        index[str(identifier)] = body
        url = comment.get("html_url")
        if url:
            index[str(url)] = body
    return index


def build_snapshot(
    comments: Sequence[Mapping[str, Any]],
    *,
    fetch_error: Optional[str] = None,
) -> ThreadSnapshot:
    """Wrap already-fetched comments into a snapshot, extracting state once."""
    fetched_at = time.time()
    if fetch_error is not None:
        return ThreadSnapshot(fetched_at=fetched_at, fetch_error=fetch_error)
    ordered = tuple(comments)
    return ThreadSnapshot(
        comments=ordered,
        state=extract_latest_state_from_comments(_state_view(ordered)),
        fetched_at=fetched_at,
    )


def state_tail_limit(env: Optional[Mapping[str, str]] = None) -> int:
    """Bound a state-only thread read to the tail that can carry the state.

    A resolver/recovery fallback only needs the latest state comment, which is
    posted near the end of the thread. The ``HISTORY_COMMENT_COUNT`` bound is
    honoured when it is larger than :data:`STATE_TAIL_FLOOR`, so every such read
    stays a single request instead of re-paginating the whole thread (issue #805,
    Pillar C).
    """
    raw = (env if env is not None else os.environ).get(HISTORY_COMMENT_COUNT_ENV)
    limit: Optional[int] = None
    if raw is not None and str(raw).strip():
        try:
            limit = int(str(raw).strip())
        except ValueError:
            limit = None
    if limit is None or limit <= 0:
        return STATE_TAIL_FLOOR
    return max(limit, STATE_TAIL_FLOOR)


def load_thread_snapshot(
    repo: str,
    issue_number: int,
    *,
    limit: Optional[int] = None,
    token: Optional[str] = None,
    transport: Optional["GitHubTransport"] = None,
) -> ThreadSnapshot:
    """Fetch (tail-bounded) the thread and extract its latest workflow state.

    Args:
        repo: Repository in ``owner/name`` format.
        issue_number: Issue/PR number.
        limit: Keep only the last ``limit`` comments (``None`` = whole thread).
        token: Optional explicit token (falls back to the environment).
        transport: Optional injected transport (tests/unification).
    """
    result = fetch_issue_comments(
        repo, issue_number, token, limit=limit, transport=transport
    )
    if not result.ok:
        return build_snapshot((), fetch_error=result.detail or "comment fetch failed")
    return build_snapshot(result.comments)
