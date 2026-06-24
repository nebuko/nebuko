#!/usr/bin/env python3
"""
Extract previous workflow state from GitHub issue comments.

Fetches the last N comments on an issue via the GitHub API, finds the
most recent bot comment containing embedded WorkflowState JSON, and
writes it to RUNNER_TEMP/prev_workflow_state.json for consumption by
resolve_config.py (auto-resume path).

Usage:
    python extract_prev_state.py --issue-number <n> [--repo <repo>]
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Optional


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_manager import extract_state_from_body, state_to_json


# Number of recent comments to scan for workflow state
MAX_COMMENTS = 50


def fetch_issue_comments(repo: str, issue_number: int) -> list[dict]:
    """Fetch comments on an issue via gh CLI."""
    token = os.environ.get("GH_TOKEN", "")
    env = {**os.environ, "GH_TOKEN": token}
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"/repos/{repo}/issues/{issue_number}/comments",
                "--jq", f".[-{MAX_COMMENTS}:]",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"Warning: gh api failed: {result.stderr.strip()}", file=sys.stderr)
            return []

        data = json.loads(result.stdout)
        return [{"id": c["id"], "body": c.get("body", ""), "user": c.get("user", {})} for c in data]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        print("Warning: gh cli not available or timed out", file=sys.stderr)
        return []


def find_latest_state(comments: list[dict]) -> Optional[str]:
    """Find the latest WorkflowState JSON from comment list.

    Scans comments in reverse chronological order (newest first) for
    the first comment containing embedded WorkflowState markers.

    Args:
        comments: List of comment dicts with at least 'body' key.

    Returns:
        Compact JSON string of WorkflowState, or None.
    """
    for comment in reversed(comments):
        body = comment.get("body", "")
        if not body:
            continue
        state = extract_state_from_body(body)
        if state:
            return state_to_json(state, compact=True)
    return None


def main():
    parser = argparse.ArgumentParser(description="Extract previous workflow state from GitHub issue comments")
    parser.add_argument("--issue-number", required=True, type=int, help="GitHub issue number")
    parser.add_argument("--repo", default="", help="Repository (owner/repo). Falls back to REPO_NAME env var.")

    args = parser.parse_args()

    repo = args.repo or os.environ.get("REPO_NAME", "")
    if not repo:
        print("Error: --repo not provided and REPO_NAME env var is empty", file=sys.stderr)
        sys.exit(1)

    if args.issue_number == 0:
        # No real issue — nothing to extract
        print("Issue number is 0 — skipping state extraction", file=sys.stderr)
        sys.exit(0)

    comments = fetch_issue_comments(repo, args.issue_number)
    if not comments:
        print("No comments fetched — no previous state", file=sys.stderr)
        sys.exit(0)

    state_json = find_latest_state(comments)
    state_file_path = os.path.join(
        os.environ.get("RUNNER_TEMP", "/tmp"), "prev_workflow_state.json"
    )

    if state_json:
        with open(state_file_path, "w", encoding="utf-8") as f:
            f.write(state_json)
        print(f"Saved previous workflow state to {state_file_path}", file=sys.stderr)
        sys.exit(0)
    else:
        # Remove stale state file if it exists
        if os.path.isfile(state_file_path):
            os.remove(state_file_path)
            print(f"Removed stale state file: {state_file_path}", file=sys.stderr)
        print("No workflow state found in recent comments", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
