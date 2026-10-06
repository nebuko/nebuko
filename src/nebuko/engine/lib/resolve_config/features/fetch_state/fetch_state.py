"""State fetching feature.

I/O adapter that retrieves the latest workflow state from issue comment
history via the GitHub API client port.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional, Sequence

import resolve_config
from workflow_state import extract_latest_state_from_comments

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only (issue #590)
    from nebuko.adapters.base import HistoryProvider
    from nebuko.engine.models import CommentEntry
    from workflow_state.workflow_state import WorkflowState

__all__ = ["_fetch_workflow_state_from_history", "extract_state", "fetch_state"]


def _fetch_workflow_state_from_history(
    repo: Optional[str],
    issue_number: Optional[int],
) -> Optional["WorkflowState"]:
    """Fetch latest workflow state from issue comment history.

    Args:
        repo: Repository in owner/name format.
        issue_number: Issue/PR number.

    Returns:
        Latest WorkflowState from comments, or None.
    """
    if not repo or not issue_number:
        return None
    try:
        # Call through the package attribute so patch("resolve_config.get_issue_comments")
        # intercepts, exactly as the old flat module did.
        comments = resolve_config.get_issue_comments(repo, issue_number)
        return extract_latest_state_from_comments(comments)
    except Exception:
        return None


# ── Engine seam (issue #590, Stage A) ─────────────────────────────


# @stub-protected
def extract_state(comments: Sequence[CommentEntry]) -> WorkflowState | None:
    """Pure state extraction from an already-fetched comment history.

    Args:
        comments: Normalized comment history, oldest first.

    Returns:
        Latest WorkflowState found in ``comments``, or ``None``.
    """
    return extract_latest_state_from_comments(
        [_as_comment_dict(comment) for comment in comments]
    )


# @stub-protected
def fetch_state(*, provider: HistoryProvider | None = None) -> WorkflowState | None:
    """Fetch the latest workflow state through an injected history provider.

    Args:
        provider: Optional ``HistoryProvider``; the default provider keeps the
            current GitHub REST fetch (via ``_fetch_workflow_state_from_history``).

    Returns:
        Latest WorkflowState, or ``None``.
    """
    if provider is None:
        return _fetch_workflow_state_from_history(
            _env_repo(), _env_issue_number()
        )
    return extract_state(provider())


def _as_comment_dict(comment: CommentEntry | dict) -> dict:
    """Coerce a normalized entry to the comment-dict shape state extraction reads."""
    if isinstance(comment, dict):
        return comment
    return {"body": getattr(comment, "body", "") or ""}


def _env_repo() -> str:
    return os.environ.get("REPO_NAME") or os.environ.get("GITHUB_REPOSITORY") or ""


def _env_issue_number() -> int:
    try:
        return int(os.environ.get("TARGET_NUMBER") or 0)
    except ValueError:
        return 0
