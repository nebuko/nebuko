import re
import json
from typing import Optional, Any

from ...workflow_state import WorkflowState, STATE_MARKER_START, STATE_MARKER_END

_STATE_JSON_RE = re.compile(
    rf"{re.escape(STATE_MARKER_START)}\s*\n(.*?)\n\s*{re.escape(STATE_MARKER_END)}",
    re.DOTALL,
)

def extract_state_from_body(body: str) -> Optional[WorkflowState]:
    """Extract WorkflowState from a single comment body string."""
    if not body:
        return None
    match = _STATE_JSON_RE.search(body)
    if match:
        json_str = match.group(1).strip()
        try:
            data = json.loads(json_str)
            return WorkflowState.model_validate(data)
        except Exception:
            return None
    return None

def _enrich_history_with_comment_ids(
    history: list[Any],  # list[HistoryEntry]
    state_comments: list[dict[str, Any]],
) -> None:
    """Set comment_id on each HistoryEntry by matching to state-containing comments.

    The i-th history entry corresponds to the i-th comment that carried a workflow
    state (discovered via index alignment rather than by agent name).
    """
    for i, entry in enumerate(history):
        if i < len(state_comments) and entry.comment_id is None:
            entry.comment_id = state_comments[i].get("id")


def extract_latest_state_from_comments(comments: list[dict[str, Any]]) -> Optional[WorkflowState]:
    """
    Extract the latest WorkflowState from a list of GitHub comments.

    Scans comments in reverse chronological order for the first comment
    containing state markers. After extraction, enriches history entries
    with comment_id values from all prior state-containing comments.

    Args:
        comments: List of comment dicts, each with at least a "body" key
            and (optionally) an "id" key from the GitHub API.

    Returns:
        Parsed WorkflowState (with enriched history) or None if no state found.
    """
    # Collect all comments containing valid WorkflowState JSON (chronological order)
    state_comments: list[dict[str, Any]] = []
    for comment in comments:
        body = comment.get("body")
        if not body:
            continue
        match = _STATE_JSON_RE.search(body)
        if match:
            json_str = match.group(1).strip()
            try:
                data = json.loads(json_str)
                WorkflowState.model_validate(data)
                state_comments.append(comment)
            except Exception:
                continue

    # Extract the latest state (last comment in reverse = most recent)
    for comment in reversed(comments):
        body = comment.get("body")
        if not body:
            continue
        match = _STATE_JSON_RE.search(body)
        if match:
            json_str = match.group(1).strip()
            try:
                data = json.loads(json_str)
                state = WorkflowState.model_validate(data)
                # Enrich history entries with comment IDs from prior state comments
                _enrich_history_with_comment_ids(state.history, state_comments)
                return state
            except Exception:
                continue
    return None