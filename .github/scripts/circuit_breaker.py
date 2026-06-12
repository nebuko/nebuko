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

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from github_client import get_issue_comments, post_comment


def count_bot_markers(comments):
    """Count bot-agent markers in comment list (excluding summary markers).

    Args:
        comments: List of comment dicts from GitHub API.

    Returns:
        Tuple of (bot_marker_count, summary_marker_count).
    """
    bot_count = 0
    summary_count = 0
    for comment in comments:
        body = comment.get("body", "")
        # Count all bot-agent markers
        bot_matches = re.findall(r"<!-- bot-agent-", body)
        # Count summary markers specifically
        summary_matches = re.findall(r"<!-- bot-agent-summary -->", body)
        bot_count += len(bot_matches)
        summary_count += len(summary_matches)
    return bot_count, summary_count


def check_circuit_breaker(
    issue_number: int,
    threshold: int,
    repo: str = "",
    gh_token: str = "",
    dry_run: bool = False,
) -> dict:
    """Evaluate circuit breaker condition.

    Args:
        issue_number: GitHub issue number.
        threshold: Max allowed agent calls before tripping.
        repo: Repository owner/repo string.
        gh_token: GitHub token for API access.
        dry_run: If True, don't actually call GitHub API.

    Returns:
        Dict with CIRCUIT_BREAKER_TRIPPED and related env vars.
    """
    if not issue_number:
        return {
            "CIRCUIT_BREAKER_TRIPPED": "false",
            "AGENT_CALLS": "0",
            "BOT_MARKER_COUNT": "0",
        }

    comments = get_issue_comments(repo, issue_number, gh_token)
    bot_count, summary_count = count_bot_markers(comments)

    # Agent calls = (total bot markers - summary markers) / 2
    # Each agent invocation produces 2 markers: one in the trigger, one in the response
    agent_calls = max(0, (bot_count - summary_count) // 2)

    result = {
        "CIRCUIT_BREAKER_TRIPPED": "true" if agent_calls >= threshold else "false",
        "AGENT_CALLS": str(agent_calls),
        "BOT_MARKER_COUNT": str(bot_count),
        "SUMMARY_MARKER_COUNT": str(summary_count),
        "CIRCUIT_BREAKER_THRESHOLD": str(threshold),
    }

    if agent_calls >= threshold and not dry_run:
        # Post warning comment
        warning = (
            f"\n⚠️ **Circuit Breaker Activated**\n\n"
            f"Agent call limit reached ({agent_calls}/{threshold}). "
            f"No further agents will be triggered until a human intervenes.\n"
        )
        try:
            post_comment(repo, issue_number, warning, gh_token, dry_run=False)
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
        default=os.environ.get("GITHUB_TOKEN", ""),
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
