"""State fetching feature.

I/O adapter that retrieves the latest workflow state from issue comment
history via the GitHub API client port.
"""

from typing import Optional

import resolve_config
from workflow_state import extract_latest_state_from_comments

__all__ = ["_fetch_workflow_state_from_history"]


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
