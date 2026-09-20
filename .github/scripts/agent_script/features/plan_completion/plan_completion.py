"""Plan-step completion marker (issue #613, G3).

When a sub-workflow entry into a branch-mounted plan workflow (issue #462)
reaches its terminal step, the parent plan needs a machine-readable signal
that this plan step is done: the ``<!-- bot-subtask-complete: {zero_index} -->``
marker comment on the parent issue, consumed by the sub-task-orchestration
mode (``extract_machine_trigger`` in ``parse_trigger.py``).

The marker is zero-indexed (``plan_step_index - 1``), matching the existing
``subtask-completion-notifier`` convention. Posting is idempotent: a marker
for an index already present among the issue's comments is never posted
again (engine recalls can re-execute the terminal path).

Core decision logic is pure; the GitHub adapter follows the ``gh`` CLI
pattern of ``ensure_pr``. Same-repo posts work cross-issue, so the
plan-orchestration same-issue case and a future cross-issue case share one
path.
"""

import json
import os
import subprocess
from typing import Optional

from openhands.sdk import get_logger

logger = get_logger(__name__)

MARKER_TEMPLATE = "<!-- bot-subtask-complete: {zero_index} -->"


def completion_marker_body(zero_index: int) -> str:
    """Marker comment body for a completed plan step (zero-indexed)."""
    if zero_index < 0:
        raise ValueError(f"zero_index must be >= 0, got {zero_index}")
    return MARKER_TEMPLATE.format(zero_index=zero_index)


def marker_already_present(
    zero_index: int,
    existing_bodies: list[str],
) -> bool:
    """True when a marker for ``zero_index`` is already in ``existing_bodies``."""
    marker = completion_marker_body(zero_index)
    return any(body and marker in body for body in existing_bodies)


def resolve_completion_index(
    parent,  # Optional[ParentRef] — duck-typed to stay import-light
    existing_bodies: list[str],
) -> Optional[int]:
    """Pure idempotency guard.

    Returns the zero-indexed plan-step index to mark complete, or ``None``
    when the marker must not be posted:
    - no parent pointer, or no ``plan_step_index`` on it;
    - a marker for that index is already present in ``existing_bodies``.
    """
    if parent is None or getattr(parent, "plan_step_index", None) is None:
        return None
    zero_index = parent.plan_step_index - 1
    if marker_already_present(zero_index, existing_bodies):
        return None
    return zero_index


def _repo() -> str:
    return os.environ.get("REPO_NAME", "")


def _parse_paginated_json_arrays(stdout: str) -> Optional[list[dict]]:
    """Parse a ``gh api --paginate`` stream into one concatenated list.

    ``--paginate`` concatenates each page's JSON document into one stream —
    ``[page1][page2]...`` — which is not a single valid JSON value. Walk the
    stream and extract each top-level ``[...]`` span (bracket-balanced,
    string-aware), then concatenate. ``None`` when no valid array was found.
    """
    comments: list[dict] = []
    in_string = False
    escaped = False
    depth = 0
    start = -1
    for pos, ch in enumerate(stdout):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            if depth > 0:
                in_string = True
            continue
        if ch == "[":
            if depth == 0:
                start = pos
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0:
                try:
                    page = json.loads(stdout[start : pos + 1])
                except ValueError:
                    return None
                if isinstance(page, list):
                    comments.extend(page)
    if not comments and start == -1:
        return []
    return comments if start != -1 else None


def fetch_existing_bodies(issue_number: int, timeout: int = 60) -> Optional[list[str]]:
    """Fetch the issue's comment bodies (best effort).

    ``None`` when the fetch failed: the caller then relies on the consuming
    side's own dedup rather than silently dropping the marker.

    ``--paginate`` is mandatory (issue #613, review round 2): on a long,
    message-polluted issue an older marker beyond the default 30-comment
    page would otherwise be missed and the marker posted twice.
    """
    result = subprocess.run(
        ["gh", "api", "--paginate",
         f"repos/{_repo()}/issues/{issue_number}/comments"],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        logger.warning(f"plan_completion: comment fetch failed: {result.stderr.strip()}")
        return None
    # ``--paginate`` concatenates each page's JSON array into one stream —
    # parse each top-level ``[...]`` span separately (a single ``json.loads``
    # would raise on a multi-page stream and silently disable the
    # idempotency guard on long issues, review round 3).
    comments = _parse_paginated_json_arrays(result.stdout or "")
    if comments is None:
        logger.warning("plan_completion: failed to parse paginated comment stream")
        return None
    return [c.get("body", "") for c in comments]


def post_completion_marker(issue_number: int, zero_index: int, timeout: int = 60) -> bool:
    """POST the marker comment to the parent issue. True on success."""
    body = completion_marker_body(zero_index)
    result = subprocess.run(
        ["gh", "api",
         f"repos/{_repo()}/issues/{issue_number}/comments",
         "-f", f"body={body}"],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        logger.warning(
            f"plan_completion: failed to post marker (index {zero_index}): "
            f"{result.stderr.strip()}"
        )
        return False
    logger.info(f"plan_completion: posted marker for index {zero_index}")
    return True


def post_terminal_plan_completion(
    parent,  # Optional[ParentRef]
    issue_number: int,
    extra_bodies: list[str] = (),
) -> bool:
    """Terminal-path entry point: guard + post in one step.

    ``extra_bodies`` covers comment sources already in memory (e.g. the
    message about to be posted this run) so a same-run duplicate is caught
    before the API fetch. Returns ``True`` only when the marker was posted.
    """
    zero_index = resolve_completion_index(parent, list(extra_bodies))
    if zero_index is None:
        return False
    existing = fetch_existing_bodies(issue_number)
    if existing is None:
        # Fetch failed: the consuming side (split-plan-state) is itself
        # idempotent, so post rather than silently drop the signal.
        logger.warning(
            "plan_completion: comment fetch failed — posting anyway, "
            "consuming side dedups via split-plan-state"
        )
        return post_completion_marker(issue_number, zero_index)
    if marker_already_present(zero_index, existing):
        logger.info("plan_completion: marker already present — skipping")
        return False
    return post_completion_marker(issue_number, zero_index)
