#!/usr/bin/env python3
"""
Prepare agent query context from GitHub issue data.

Reads the GitHub event payload, fetches issue comments via github_client,
extracts bot-agent-summary markers, builds a history string, formats it
against a template, and writes the result to an output file.

Also extracts workflow state from collapsed JSON sections in comments and
injects structured history into the agent context.

Usage:
    python query_context.py --issue-number <n> [--output-file <path>] \\
        [--event-path <path>]

Output:
    KEY=value pairs to stdout (for GITHUB_ENV consumption). Only small scalar
    values are emitted; large payloads (issue body, comment history) are
    written to files under /tmp/agent_query and exposed via *_PATH variables.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only (issue #590)
    from adapters.base import HistoryProvider
    from engine.models import CommentEntry, Trigger


from features.select_history_comments.history import (
    HistoryWindowConfig,
    select_history,
)
from features.select_history_comments.select_history_comments import (
    select_history_comments,
)
from github_client import get_comment_body, get_issue_comments
from workflow_state import (
    extract_latest_state_from_comments as extract_state,
    extract_state_from_body,
)
from workflow_state.state_codec import HANDOFF_FILENAME, WorkflowStateCodec
from workflow_state.features.comment.to_git_comment import strip_state_section


DEFAULT_TEMPLATE = (
    "## Issue Context\n\n"
    "**Title:** {title}\n"
    "**Author:** {author}\n\n"
    "---\n\n"
    "{body}\n\n"
    "---\n\n"
    "{state_context}"
    "## History\n\n"
    "{history}"
)

# Max UTF-8 byte size for a single value emitted into $GITHUB_ENV.
# OpenHands mirrors os.environ into tmux via one `tmux set-environment <k> <v>`
# subprocess per variable; a single oversized value makes the kernel reject the
# execve (E2BIG -> "command too long"). Oversized values must go to files
# instead (issue #520).
MAX_GITHUB_ENV_VALUE_BYTES = 64 * 1024

# Directory where oversized/large context payloads are written as files.
CONTEXT_FILES_DIR = Path("/tmp/agent_query")

# Max characters rendered for a non-pinned comment that carries no
# bot-agent-summary marker. Keeps the history bounded without dropping the
# comment entirely (issue #617).
HISTORY_EXCERPT_CHARS = 400

# Suffix appended to a truncated excerpt to signal elision.
_EXCERPT_ELLIPSIS = "…"


def _check_env_value_size(key: str, value: str) -> None:
    """Refuse to emit a $GITHUB_ENV value larger than MAX_GITHUB_ENV_VALUE_BYTES."""
    size = len(value.encode("utf-8"))
    if size > MAX_GITHUB_ENV_VALUE_BYTES:
        raise RuntimeError(
            f"Refusing to emit {key} into $GITHUB_ENV: {size} bytes exceeds "
            f"{MAX_GITHUB_ENV_VALUE_BYTES} (would break tmux set-environment). "
            f"Write the payload to a file under {CONTEXT_FILES_DIR} and emit its path instead."
        )


def _write_context_file(filename: str, content: str) -> str:
    """Write a context payload to CONTEXT_FILES_DIR and return the file path.

    Used for large payloads (issue body, comment history) that must stay out
    of job-level environment variables.
    """
    CONTEXT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    path = CONTEXT_FILES_DIR / filename
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {len(content)} chars to {path}", file=sys.stderr)
    return str(path)


def load_event_data(event_path: str) -> dict:
    """Read event JSON and extract issue title, body, and author.

    Args:
        event_path: Path to the GitHub event JSON file.

    Returns:
        Dict with keys 'title', 'body', 'author'. Missing fields default to ''.
    """
    with open(event_path, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    issue = event_data.get("issue", {})
    return {
        "title": issue.get("title", ""),
        "body": issue.get("body", ""),
        "author": issue.get("user", {}).get("login", ""),
    }


def extract_summary_from_comment(comment_body: str):
    """Extract summary text from a comment using the bot-agent-summary marker.

    Uses regex r'<!--\\s*bot-agent-summary\\s*-->(.*?)^---' with
    re.DOTALL | re.MULTILINE flags.

    Args:
        comment_body: Raw comment body text.

    Returns:
        Stripped summary text if marker found, else None.
    """
    match = re.search(
        r"<!--\s*bot-agent-summary\s*-->(.*?)^---",
        comment_body,
        re.DOTALL | re.MULTILINE,
    )
    if match:
        return match.group(1).strip()
    return None


def format_query_template(title, body, author, history, template_str, state_context=""):
    """Replace placeholders in template string with provided values.

    Supports {title}, {body}, {author}, {history}, {state_context} placeholders.
    Falls back to empty string for any missing values.

    Args:
        title: Issue title.
        body: Issue body.
        author: Issue author login.
        history: Comment history string.
        template_str: Template string with placeholders.
        state_context: Workflow state context string (default: "").

    Returns:
        Formatted string with all placeholders replaced.
    """
    return template_str.format(
        title=title or "",
        body=body or "",
        author=author or "",
        history=history or "",
        state_context=state_context or "",
    )


def _resolve_repo_name() -> str:
    """Resolve repository name in owner/repo format.

    Tries git config first, falls back to GITHUB_REPOSITORY env var.

    Returns:
        Repository name string (e.g. 'owner/repo').
    """
    # Try git remote origin url first
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=True,
        )
        url = result.stdout.strip()
        # Parse ssh format: git@github.com:owner/repo.git
        if ":" in url and ".git" in url:
            return url.split(":")[-1].replace(".git", "")
        # Parse https format: https://github.com/owner/repo.git
        if "github.com" in url:
            parts = url.split("github.com/")[-1].replace(".git", "")
            return parts
    except (subprocess.CalledProcessError, IndexError):
        pass

    # Fall back to GITHUB_REPOSITORY env var
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return repo

    print("Warning: Could not resolve repo name", file=sys.stderr)
    return ""


def _get_injectable_comment_ids(state) -> set[int] | None:
    """Extract comment IDs from workflow history where inject_context=True.

    Returns None if no state is available (signals: include all comments).

    Args:
        state: WorkflowState instance, or None.

    Returns:
        Set of comment IDs eligible for injection, or None.
    """
    if state is None:
        return None

    ids: set[int] = set()
    for entry in state.history:
        if entry.inject_context:
            ids.update(cid for cid in entry.comment_ids if cid is not None)
    return ids


def _is_bot_comment(comment: dict) -> bool:
    """Check if a comment was authored by a bot/agent rather than a human user.

    User comments must NEVER be filtered out — they contain requirements,
    feedback, and clarifications that the agent needs to see.
    Only bot/agent comments are subject to inject_context filtering.

    Args:
        comment: Comment dict from GitHub API (contains 'user' key).

    Returns:
        True if the comment was authored by a bot agent.
    """
    user = comment.get("user", {})
    return user.get("type") == "Bot" or "[bot]" in user.get("login", "")


def _filter_comments_by_inject_context(comments, state) -> list[dict]:
    """Filter comments based on workflow_state.history inject_context settings.

    **User comments are NEVER filtered** — they always pass through regardless
    of inject_context settings. Only bot-authored comments (agents) are subject
    to the inject_context whitelist.

    If state exists, bot comments whose ID does not appear in history entries
    with inject_context=True are excluded. If no state is available, returns
    all comments.

    Args:
        comments: List of comment dicts from GitHub API.
        state: WorkflowState instance, or None.

    Returns:
        Filtered list of comment dicts.
    """
    allowed_ids = _get_injectable_comment_ids(state)
    if allowed_ids is None:
        return comments

    return [c for c in comments if not _is_bot_comment(c) or c.get("id") in allowed_ids]


def _excerpt(text: str, limit: int = HISTORY_EXCERPT_CHARS) -> str:
    """Truncate ``text`` to ``limit`` characters, marking any elision."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + _EXCERPT_ELLIPSIS


def _render_history_comment(comment: dict, *, verbatim: bool) -> str:
    """Render one comment as a history section, or '' when it has no content.

    Pinned (``verbatim=True``) comments keep their full body; all others use
    the ``<!-- bot-agent-summary -->`` summary when present, otherwise a
    bounded excerpt. The collapsed state section is always stripped first.
    """
    body = comment.get("body", "")
    author = comment.get("user", {}).get("login", "unknown")

    if verbatim:
        content = strip_state_section(body)
    else:
        summary = extract_summary_from_comment(body)
        content = summary if summary is not None else _excerpt(strip_state_section(body))

    if not content.strip():
        return ""
    return f"### {author} a écrit :\n{content}\n---\n"


def _build_history_string(comments, state=None, history_comment_count=None) -> str:
    """Build formatted history string from comments.

    Only the last ``history_comment_count`` comments overall (default 5) are
    injected, unioned with every pinned comment; a pinned comment inside the
    window is de-duplicated (issue #683). Pinned comments (per
    ``state.pinned_comment_ids`` or a ``pin=True`` history entry) render their
    full body; every other comment renders a bot-agent-summary when present,
    otherwise an excerpt capped by ``HISTORY_EXCERPT_CHARS``. State sections
    (collapsed JSON) are stripped from the visible history to reduce noise, but
    structured state is extracted separately.

    Args:
        comments: List of comment dicts from GitHub API.
        state: WorkflowState whose pinning decides verbatim vs. summary.
            ``None`` summarizes every comment.
        history_comment_count: Raw ``history_comment_count`` workflow input
            (``int``/``str``/``None``); ``None`` or invalid falls back to 5.

    Returns:
        Formatted history string.
    """
    if not comments:
        return "No previous comments"

    pinned_ids = select_history_comments(comments, state).verbatim_ids
    selected = select_history(
        comments,
        pinned_ids,
        HistoryWindowConfig(history_comment_count=history_comment_count),
    ).all_comments()

    parts = []
    for comment in selected:
        rendered = _render_history_comment(
            comment, verbatim=comment.get("id") in pinned_ids
        )
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)


def _build_state_context(comments, repo: str) -> str:
    """Extract the latest workflow state and format it as context for agents.

    Scans comments in reverse for the latest workflow state JSON section.
    If found, formats a compact summary of the state including history and
    pinned references. Pinned comment bodies are fetched and included in full.

    Args:
        comments: List of comment dicts from GitHub API.
        repo: Repository in format owner/repo.

    Returns:
        Formatted state context string (empty if no state found).
    """
    for comment in reversed(comments):
        body = comment.get("body", "")
        state = extract_state_from_body(body)
        if state:
            lines = [
                "## Workflow State",
                "",
                f"- **Workflow:** {state.workflow}",
                f"- **Step:** {state.step}",
                f"- **Agent:** {state.agent}",
                f"- **Version:** {state.version}",
            ]
            if state.phase:
                lines.append(f"- **Phase:** {state.phase}")
            if state.position:
                path = "|".join(f"{r.workflow}/{r.step}" for r in state.position)
                lines.append(f"- **Position:** `{path}`")
            if state.history:
                lines.append("")
                lines.append("**Execution History:**")
                lines.append("")
                for entry in state.history:
                    pin_marker = " 📌" if entry.pin else ""
                    lines.append(
                        f"- [{entry.agent}/{entry.step}]"
                        f"{pin_marker}: {entry.summary[:120]}"
                    )
            if state.pinned_comment_ids:
                # Fetch full content of pinned comments
                pinned_contents = []
                for cid in state.pinned_comment_ids:
                    content = get_comment_body(str(cid), repo)
                    if content:
                        pinned_contents.append(
                            f"### Pinned Comment #{cid}\n\n{content}"
                        )
                if pinned_contents:
                    lines.append("")
                    lines.append("**Pinned Context:**")
                    lines.append("")
                    lines.extend(pinned_contents)
            if state.current_context:
                lines.append("")
                lines.append("**Current Context:**")
                lines.append("")
                for k, v in state.current_context.items():
                    lines.append(f"- **{k}:** {v}")
            if state.dispatch:
                d = state.dispatch
                lines.append("")
                lines.append("**Dispatch:**")
                lines.append("")
                lines.append(f"- **Status:** {d.status}")
                if d.task_hint:
                    lines.append(f"- **Task Hint:** {d.task_hint}")
                if d.complexity and d.complexity != "medium":
                    lines.append(f"- **Complexity:** {d.complexity}")
                if d.reasoning and d.reasoning != "none":
                    lines.append(f"- **Reasoning:** {d.reasoning}")
                if d.reason:
                    lines.append(f"- **Reason:** {d.reason}")
            lines.append("")
            lines.append("---\n\n")
            return "\n".join(lines)
    return ""


# ── Engine seam (issue #590, Stage A) ─────────────────────────────

# Bot bodies carry one of the standard markers; the seam uses them to classify
# injected ``CommentEntry`` history the same way ``_is_bot_comment`` classifies
# GitHub API payloads (so ``inject_context`` filtering is unchanged).
_BOT_MARKER_RE = re.compile(r"<!--\s*bot-(?:workflow-terminal|agent-[a-z0-9-]+)\s*-->")


def _entry_to_comment(entry, index: int) -> dict:
    """Coerce a normalized ``CommentEntry`` to the GitHub comment-dict shape.

    The dict shape is what the history/state helpers already read, so the seam
    reuses them verbatim. Comment ids are synthetic (1-based) — ``CommentEntry``
    carries none, and only uniqueness matters for pinning/window selection.
    """
    author = getattr(entry, "author", "") or ""
    body = getattr(entry, "body", "") or ""
    is_bot = author == "bot" or bool(_BOT_MARKER_RE.search(body))
    return {
        "id": index + 1,
        "body": body,
        "created_at": getattr(entry, "created_at", "") or "",
        "user": {"login": author, "type": "Bot" if is_bot else "User"},
    }


def _trigger_event_data(trigger, event_path: str | None) -> dict:
    """Resolve the (title, body, author) triple for the rendered query.

    ``--event-path`` wins when supplied (the legacy source). Otherwise the
    values come from the trigger's raw payload — the issue fields a
    comment-level trigger does not itself carry — falling back to the trigger.
    """
    if event_path:
        return load_event_data(event_path)
    payload = getattr(trigger, "payload", None) or {}
    issue = payload.get("issue") or {}
    return {
        "title": issue.get("title", "") or "",
        "body": issue.get("body", "") or getattr(trigger, "body", "") or "",
        "author": (issue.get("user") or {}).get("login", "")
        or getattr(trigger, "author", "")
        or "",
    }


# @stub-protected
def query_context(
    trigger: Trigger,
    *,
    history: Sequence[CommentEntry] | None = None,
    event_path: str | None = None,
) -> str:
    """Build the ready agent prompt from a normalized trigger.

    Args:
        trigger: Normalized trigger (replaces the ``--issue-number``-only entry).
        history: Optional injected comment history; when ``None`` it is fetched
            through the default provider (unchanged GHA behaviour).
        event_path: Optional GitHub event JSON path (``--event-path`` retained).

    Returns:
        The rendered ready-prompt text.
    """
    repo_name = _resolve_repo_name()
    if history is not None:
        comments = [_entry_to_comment(entry, index) for index, entry in enumerate(history)]
    else:
        comments = get_issue_comments(repo_name, trigger.number)

    event_data = _trigger_event_data(trigger, event_path)

    prev_state = None
    try:
        prev_state = extract_state(comments)
    except Exception as e:
        print(f"Warning: Failed to extract state for inject_context: {e}", file=sys.stderr)

    filtered_comments = _filter_comments_by_inject_context(comments, prev_state)
    history_str = _build_history_string(
        filtered_comments,
        prev_state,
        history_comment_count=os.environ.get("HISTORY_COMMENT_COUNT"),
    )
    # Unfiltered comments: the state block may live in a comment excluded from
    # the history display by inject_context=False (see main()).
    state_context = _build_state_context(comments, repo_name)

    template_str = os.environ.get("ISSUE_TEMPLATE", DEFAULT_TEMPLATE)
    return format_query_template(
        title=event_data["title"],
        body=event_data["body"],
        author=event_data["author"],
        history=history_str,
        template_str=template_str,
        state_context=state_context,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Prepare agent query context from GitHub issue data"
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        required=True,
        help="GitHub issue number",
    )
    parser.add_argument(
        "--output-file",
        default="/tmp/ready_prompt.md",
        help="Path to write formatted query output (default: /tmp/ready_prompt.md)",
    )
    parser.add_argument(
        "--event-path",
        default=os.environ.get("GITHUB_EVENT_PATH"),
        help="Path to GitHub event JSON file",
    )

    args = parser.parse_args()

    # Resolve repo name
    repo_name = _resolve_repo_name()

    # Load event data
    event_data = load_event_data(args.event_path)

    # Fetch comments
    comments = get_issue_comments(repo_name, args.issue_number)

    # Extract workflow state FIRST (needed for inject_context filtering)
    prev_state = None
    try:
        prev_state = extract_state(comments)
    except Exception as e:
        print(f"Warning: Failed to extract state for inject_context: {e}", file=sys.stderr)

    # Filter comments by inject_context settings from workflow history
    filtered_comments = _filter_comments_by_inject_context(comments, prev_state)
    if filtered_comments is not comments:
        skipped = len(comments) - len(filtered_comments)
        print(f"Filtered {skipped} comments based on inject_context settings", file=sys.stderr)

    # Build history string from filtered comments; only the last N comments are
    # injected (default 5, issue #683), pinned comments stay verbatim and are
    # always added on top, everything else is summarized/excerpted (issue #617).
    history = _build_history_string(
        filtered_comments,
        prev_state,
        history_comment_count=os.environ.get("HISTORY_COMMENT_COUNT"),
    )

    # Build structured state context from latest workflow state (fetches pinned comment bodies)
    # NOTE: Intentionally passes UNFILTERED comments — _build_state_context scans ALL comments
    # to locate the latest workflow state JSON (which may reside in a comment excluded from
    # history display by inject_context=False). Filtering applies only to history display.
    state_context = _build_state_context(comments, repo_name)

    # Save extracted workflow state as JSON for the next agent step
    # Use RUNNER_TEMP for per-run isolation (avoids cross-contamination on self-hosted runners)
    state_file_path = os.path.join(
        os.environ.get("RUNNER_TEMP", "/tmp"), HANDOFF_FILENAME
    )
    try:
        if prev_state:
            WorkflowStateCodec().save_prev(prev_state)
            print(f"Saved previous workflow state to {state_file_path} (v{prev_state.version})", file=sys.stderr)
        else:
            if os.path.isfile(state_file_path):
                os.remove(state_file_path)
                print(f"Removed stale state file: {state_file_path}", file=sys.stderr)
            print("No previous workflow state found in comments — starting fresh", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to save previous state: {e}", file=sys.stderr)

    # Inject recovery log if a previous agent failed mid-execution
    recovery_log = ""
    if prev_state and prev_state.current_context and "recovery_log" in prev_state.current_context:
        recovery_log = prev_state.current_context["recovery_log"]
        print(f"Detected recovery log from previous failed run ({len(recovery_log)} chars)", file=sys.stderr)

    # Format template
    template_str = os.environ.get("ISSUE_TEMPLATE", DEFAULT_TEMPLATE)
    query = format_query_template(
        title=event_data["title"],
        body=event_data["body"],
        author=event_data["author"],
        history=history,
        template_str=template_str,
        state_context=state_context,
    )

    # Append recovery log right before ## History section if present
    if recovery_log:
        history_marker = "\n## History\n"
        if history_marker in query:
            query = query.replace(history_marker, f"\n{recovery_log}\n## History\n", 1)
        else:
            query += f"\n{recovery_log}\n"
        print("Injected recovery log into agent query context", file=sys.stderr)

    # Append human resume input if present (workflow was paused, human provided input)
    # The BARE_RESUME_INPUT sentinel marks a bare /nebuko resume (no feedback
    # text); it is an authorization signal for human_gate steps only (issue
    # #506) and must not leak into the prompt as human feedback.
    try:
        from resolve_config.features.parse_trigger.parse_trigger import BARE_RESUME_INPUT
    except ImportError:
        BARE_RESUME_INPUT = "__resumed__"
    human_resume_input = os.environ.get("HUMAN_RESUME_INPUT", "").strip()
    if human_resume_input and human_resume_input != BARE_RESUME_INPUT:
        history_marker = "\n## History\n"
        resume_section = f"\n## Human Resume Input\n\n{human_resume_input}\n\n"
        if history_marker in query:
            query = query.replace(history_marker, f"{resume_section}## History\n", 1)
        else:
            query += f"{resume_section}"
        print("Injected human resume input into agent query context", file=sys.stderr)

    # Write output file
    with open(args.output_file, "w", encoding="utf-8") as f:
        f.write(query)

    # ─────────────────────────────────────────────────────────────
    # Output to stdout for >> $GITHUB_ENV consumption
    #
    # Only small scalar values may be emitted into the environment: OpenHands
    # mirrors os.environ into tmux (one `set-environment <k> <v>` per variable)
    # and a single value > ~128 KiB makes the kernel reject the execve with
    # E2BIG ("command too long") — issue #520. Large payloads (issue body,
    # comment history) are written to files and exposed via *_PATH variables.
    # ─────────────────────────────────────────────────────────────
    body_val = event_data["body"].strip()
    history_val = history.strip()

    print(f"AGENT_QUERY_PATH={args.output_file}")
    _check_env_value_size("ISSUE_TITLE", event_data["title"])
    print(f"ISSUE_TITLE={event_data['title']}")
    _check_env_value_size("ISSUE_AUTHOR", event_data["author"])
    print(f"ISSUE_AUTHOR={event_data['author']}")

    body_path = _write_context_file("issue_body.md", body_val)
    history_path = _write_context_file("comment_history.md", history_val)
    print(f"ISSUE_BODY_PATH={body_path}")
    print(f"COMMENT_HISTORY_PATH={history_path}")

    # Log to stderr only (won't interfere with GITHUB_ENV)
    print(f"Wrote agent query context to {args.output_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
