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
    KEY=value pairs to stdout (for GITHUB_ENV consumption).
    Multi-line values use heredoc format (KEY<<EOF ... EOF).
"""

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from github_client import get_issue_comments
from state_manager import (
    extract_state_from_body,
    strip_state_section,
)


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


def _build_history_string(comments) -> str:
    """Build formatted history string from comments.

    For each comment, tries to extract a bot-agent-summary. If found, uses
    the summary; otherwise strips the state section and uses the raw comment body.
    State sections (collapsed JSON) are stripped from the visible history to
    reduce noise, but structured state is extracted separately.

    Args:
        comments: List of comment dicts from GitHub API.

    Returns:
        Formatted history string.
    """
    if not comments:
        return "No previous comments"

    parts = []
    for comment in comments:
        body = comment.get("body", "")
        author = comment.get("user", {}).get("login", "unknown")
        summary = extract_summary_from_comment(body)
        if summary is not None:
            content = summary
        else:
            # Strip the collapsed state JSON section to reduce noise in history
            content = strip_state_section(body)
        if not content.strip():
            continue
        parts.append(f"### {author} a écrit :\n{content}\n---\n")

    return "\n".join(parts)


def _build_state_context(comments) -> str:
    """Extract the latest workflow state and format it as context for agents.

    Scans comments in reverse for the latest workflow state JSON section.
    If found, formats a compact summary of the state including history and
    pinned references.

    Args:
        comments: List of comment dicts from GitHub API.

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
                lines.append("")
                lines.append(
                    "**Pinned Comment IDs:** "
                    f"{', '.join(str(c) for c in state.pinned_comment_ids)}"
                    " *(full content should be fetched at runtime)*"
                )
            if state.current_context:
                lines.append("")
                lines.append("**Current Context:**")
                lines.append("")
                for k, v in state.current_context.items():
                    lines.append(f"- **{k}:** {v}")
            lines.append("")
            lines.append("---\n\n")
            return "\n".join(lines)
    return ""


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

    # Build history string
    history = _build_history_string(comments)

    # Build structured state context from latest workflow state
    state_context = _build_state_context(comments)

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

    # Write output file
    with open(args.output_file, "w", encoding="utf-8") as f:
        f.write(query)

    # ─────────────────────────────────────────────────────────────
    # Output to stdout for >> $GITHUB_ENV consumption
    # ─────────────────────────────────────────────────────────────
    print(f"AGENT_QUERY_PATH={args.output_file}")
    print(f"ISSUE_TITLE={event_data['title']}")
    print(f"ISSUE_AUTHOR={event_data['author']}")

    # Strip trailing whitespace/newlines to prevent parser confusion
    body_val = event_data["body"].strip()
    if "\n" in body_val:
        print("ISSUE_BODY<<BODY_HEREDOC")
        print(body_val)
        print("BODY_HEREDOC")
    else:
        print(f"ISSUE_BODY={body_val}")

    history_val = history.strip()
    if "\n" in history_val:
        print("COMMENT_HISTORY<<HISTORY_HEREDOC")
        print(history_val)
        print("HISTORY_HEREDOC")
    else:
        print(f"COMMENT_HISTORY={history_val}")

    # Log to stderr only (won't interfere with GITHUB_ENV)
    print(f"Wrote agent query context to {args.output_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
