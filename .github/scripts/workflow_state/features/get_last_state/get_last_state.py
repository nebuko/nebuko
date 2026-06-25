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

def extract_latest_state_from_comments(comments: list[dict[str, Any]]) -> Optional[WorkflowState]:
    """
    Extract the latest WorkflowState from a list of GitHub comments.

    Scans comments in reverse chronological order for the first comment
    containing state markers. Returns None if no state is found.

    Args:
        comments: List of comment dicts, each with at least a "body" key.

    Returns:
        Parsed WorkflowState or None if no state found.
    """
    for comment in reversed(comments):
        body = comment.get("body")
        if not body:
            continue
        match = _STATE_JSON_RE.search(body)
        if match:
            json_str = match.group(1).strip()
            try:
                data = json.loads(json_str)
                return WorkflowState.model_validate(data)
            except Exception:
                continue
    return None