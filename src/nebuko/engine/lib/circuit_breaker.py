#!/usr/bin/env python3
"""
Check circuit breaker - prevent excessive agent calls.

Migrates 'Check Circuit Breaker' bash block.

Fetches recent comments, counts bot markers, computes agent call count
against a configurable threshold. Posts a warning if exceeded.

Usage:
    python circuit_breaker.py --issue-number <n> --threshold <int> \\
        [--repo <owner/repo>] [--gh-token <token>] [--dry-run]

Output:
    CIRCUIT_BREAKER_TRIPPED=true/false to stdout.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import TYPE_CHECKING, Mapping, Sequence

from nebuko.engine.lib.github_client import get_issue_comments, post_comment

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only (issue #590)
    from nebuko.engine.models import CommentEntry


def count_bot_markers(comments) -> int:
    """Count ``<!-- bot-agent- -->`` markers across the comment list.

    Args:
        comments: List of comment dicts from GitHub API, or normalized
            ``CommentEntry``-shaped objects (engine seam, issue #590).

    Returns:
        Total number of bot-agent markers found.
    """
    bot_count = 0
    for comment in comments:
        bot_count += len(re.findall(r"<!-- bot-agent-", _comment_body(comment)))
    return bot_count


def _comment_body(comment) -> str:
    """Read a comment body from either an API dict or a normalized entry."""
    if isinstance(comment, Mapping):
        return comment.get("body", "") or ""
    return getattr(comment, "body", "") or ""


def circuit_breaker_warning(agent_calls: int, threshold: int) -> str:
    """Build the warning comment posted once the breaker trips."""
    return (
        f"\n⚠️ **Circuit Breaker Activated**\n\n"
        f"Agent call limit reached ({agent_calls}/{threshold}). "
        f"No further agents will be triggered until a human intervenes.\n\n"
        f"To raise the limit for this issue, a human can run "
        f"`/nebuko update circuit-breaker --value <n>`.\n"
    )


def check_circuit_breaker(
    issue_number: int,
    threshold: int,
    repo: str = "",
    gh_token: str = "",
    dry_run: bool = False,
    history: Sequence[CommentEntry] | None = None,
) -> dict:
    """Evaluate circuit breaker condition.

    Args:
        issue_number: GitHub issue number.
        threshold: Max allowed agent calls before tripping.
        repo: Repository owner/repo string.
        gh_token: GitHub token for API access.
        dry_run: If True, don't actually call GitHub API.
        history: Optional injected comment history (engine seam, issue #590);
            when ``None`` the current REST fetch is used unchanged.

    Returns:
        Dict with CIRCUIT_BREAKER_TRIPPED and related env vars.
    """
    if not issue_number:
        return {
            "CIRCUIT_BREAKER_TRIPPED": "false",
            "AGENT_CALLS": "0",
            "BOT_MARKER_COUNT": "0",
        }

    if history is not None:
        comments = list(history)
    else:
        comments = get_issue_comments(repo, issue_number, gh_token, dry_run=dry_run)
    bot_count = count_bot_markers(comments)

    # Each agent invocation produces 2 markers: one in the trigger, one in the
    # response.
    agent_calls = max(0, bot_count // 2)

    result = {
        "CIRCUIT_BREAKER_TRIPPED": "true" if agent_calls >= threshold else "false",
        "AGENT_CALLS": str(agent_calls),
        "BOT_MARKER_COUNT": str(bot_count),
        "CIRCUIT_BREAKER_THRESHOLD": str(threshold),
    }

    if agent_calls >= threshold and not dry_run:
        # Post warning comment
        warning = circuit_breaker_warning(agent_calls, threshold)
        try:
            post_comment(repo, issue_number, warning, gh_token)
        except Exception as e:
            print(f"Failed to post circuit breaker warning: {e}", file=sys.stderr)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Check circuit breaker - prevent excessive agent calls"
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=0,
        help="GitHub issue number",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=8,
        help="Max allowed agent calls before tripping",
    )
    parser.add_argument(
        "--repo",
        default="",
        help="Repository owner/repo string",
    )
    parser.add_argument(
        "--gh-token",
        default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""),
        help="GitHub token",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode (no actual API calls)",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Path to GITHUB_ENV file to append results",
    )

    args = parser.parse_args()

    result = check_circuit_breaker(
        issue_number=args.issue_number,
        threshold=args.threshold,
        repo=args.repo,
        gh_token=args.gh_token,
        dry_run=args.dry_run,
    )

    for key, value in result.items():
        print(f"{key}={value}")

    if args.env_file:
        with open(args.env_file, "a") as f:
            for key, value in result.items():
                f.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
