import re
import json
from typing import Optional, Any

from ...workflow_state import WorkflowState, STATE_MARKER_START, STATE_MARKER_END

_STATE_JSON_RE = re.compile(
    rf"{re.escape(STATE_MARKER_START)}\s*\n(.*?)\n\s*{re.escape(STATE_MARKER_END)}",
    re.DOTALL,
)

# Multi-part continuation header written by build_output for message parts 2..N:
# "*(Part {i}/{total})*" (issue #504).
_PART_HEADER_RE = re.compile(r"^\s*\*\(Part \d+/\d+\)\*\s*$", re.MULTILINE)

_AGENT_MARKER_RE = re.compile(r"<!-- bot-agent-([a-z0-9\-]+) -->")


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

def _agent_marker_of(comment: dict[str, Any]) -> Optional[str]:
    """Return the ``bot-agent-{role}`` marker value of a comment, or None."""
    match = _AGENT_MARKER_RE.search(comment.get("body") or "")
    return match.group(1) if match else None


def _is_part_comment(comment: dict[str, Any]) -> bool:
    """True if the comment is a multi-part continuation fragment (part 2..N).

    Continuation parts carry the part header and have no state section, so
    they are naturally excluded from the state-comment set (issue #504).
    """
    body = comment.get("body") or ""
    return bool(_PART_HEADER_RE.search(body)) and not _STATE_JSON_RE.search(body)


def _enrich_history_with_comment_ids(
    history: list[Any],  # list[HistoryEntry]
    state_comments: list[dict[str, Any]],
    all_comments: Optional[list[dict[str, Any]]] = None,
) -> None:
    """Set comment_ids on each HistoryEntry from the comments that produced it.

    The i-th history entry corresponds to the i-th comment that carried a
    workflow state (index alignment). In addition, continuation parts of a
    multi-part message — the same-agent ``*(Part i/N)*`` comments posted
    right after the state comment — are appended to that entry's
    comment_ids (issue #504). Stored (non-empty) comment_ids are preserved;
    only empty entries are enriched.
    """
    if all_comments is None:
        all_comments = []

    # Seed each entry's group with its aligned state comment ID (index alignment),
    # then append any same-agent continuation parts that follow it chronologically.
    # (issue #504)
    part_groups: list[list[Any]] = [[c.get("id")] for c in state_comments]
    state_indexes = {id(c): i for i, c in enumerate(state_comments)}
    current_group: Optional[list[Any]] = None
    current_marker: Optional[str] = None
    for comment in all_comments:
        if id(comment) in state_indexes:
            # Re-establish the group boundary at the aligned state comment.
            current_group = part_groups[state_indexes[id(comment)]]
            current_marker = _agent_marker_of(comment)
        elif _is_part_comment(comment):
            if current_group is None:
                continue
            marker = _agent_marker_of(comment)
            if current_marker is not None and marker is not None and marker != current_marker:
                continue
            cid = comment.get("id")
            if cid is not None:
                current_group.append(cid)

    for i, entry in enumerate(history):
        if i >= len(part_groups):
            break
        if entry.comment_ids:
            continue
        entry.comment_ids = [cid for cid in part_groups[i] if cid is not None]


def _enrich_pinned_comment_ids(
    history: list[Any],  # list[HistoryEntry]
    pinned_comment_ids: list[int],
) -> None:
    """Union the comment_ids of every ``pin=True`` history entry into
    ``pinned_comment_ids`` (issue #617).

    Downstream context assembly (_build_state_context) re-fetches the full
    bodies of these IDs verbatim. Idempotent: existing IDs and duplicates are
    skipped, so re-running on an already-enriched state is a no-op.
    """
    seen = set(pinned_comment_ids)
    for entry in history:
        if not getattr(entry, "pin", False):
            continue
        for cid in entry.comment_ids or []:
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            pinned_comment_ids.append(cid)


def _enrich_step_outputs_with_comment_ids(
    state: WorkflowState,
    state_comments: list[dict[str, Any]],
) -> None:
    """Backfill step-output locators with the comment that carried each step.

    A step's output is its posted comment (issue #756, ADR-0013). The state
    embedded in a comment cannot name its own comment id (chicken-and-egg), so
    the read path fills it in by index alignment: each state comment's embedded
    ``step`` maps to that comment's id. A stored ``comment_id`` is preserved for
    steps absent from the thread view; a newer comment for the same step wins.
    """
    from ...step_output_record import StepOutputRecord

    for comment in state_comments:
        comment_id = comment.get("id")
        if comment_id is None:
            continue
        embedded = extract_state_from_body(comment.get("body") or "")
        if embedded is None or not embedded.step:
            continue
        StepOutputRecord(step=embedded.step, comment_id=comment_id).upsert_into(
            state.current_context
        )


def extract_latest_state_from_comments(comments: list[dict[str, Any]]) -> Optional[WorkflowState]:
    """
    Extract the latest WorkflowState from a list of GitHub comments.

    Scans comments in reverse chronological order for the first comment
    containing state markers. After extraction, enriches history entries
    with comment_ids values: the aligned state comment plus any
    multi-part continuation fragments of each step's message.

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
                _enrich_history_with_comment_ids(state.history, state_comments, comments)
                # Backfill step-output locators with each step's posted comment
                _enrich_step_outputs_with_comment_ids(state, state_comments)
                # Derive pinned_comment_ids from pin=True entries (issue #617)
                _enrich_pinned_comment_ids(state.history, state.pinned_comment_ids)
                return state
            except Exception:
                continue
    return None