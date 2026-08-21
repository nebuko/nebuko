#!/usr/bin/env python3
"""
Check summarizer gate - determine if summarizer should be triggered.

Migrates 'Check Summarizer Gate' bash block.

Counts non-bot-user comments and checks if a summary comment already exists.
Triggers summarizer when threshold is met and no existing summary.

Usage:
    python summarizer_gate.py --issue-number <n> --min-comments <int> \\
        [--repo <owner/repo>] [--gh-token <token>] [--dry-run]

Output:
    SUMMARIZER_TRIGGERED=true/false to stdout.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from github_client import BOT_MARKER, get_issue_comments


def has_summary_comment(comments):
    """Check if any comment contains the bot-agent-summary marker.

    Args:
        comments: List of comment dicts from GitHub API.

    Returns:
        True if a summary comment already exists.
    """
    for comment in comments:
        body = comment.get("body", "")
        if "<!-- bot-agent-summary -->" in body:
            return True
    return False


def count_non_bot_comments(comments):
    """Count comments that are not bot-generated.

    A comment is bot-generated if it carries either the universal bot
    marker (<!-- bot-workflow-terminal -->) or the legacy agent marker
    (<!-- bot-agent-...).

    Args:
        comments: List of comment dicts from GitHub API.

    Returns:
        Number of non-bot comments.
    """
    count = 0
    for comment in comments:
        body = comment.get("body", "")
        if "<!-- bot-agent-" not in body and BOT_MARKER not in body:
            count += 1
    return count


def check_summarizer_gate(
    issue_number: int,
    min_comments: int,
    repo: str = "",
    gh_token: str = "",
    dry_run: bool = False,
) -> dict:
    """Evaluate summarizer gate condition.

    Args:
        issue_number: GitHub issue number.
        min_comments: Minimum non-bot comments needed to trigger summarizer.
        repo: Repository owner/repo string.
        gh_token: GitHub token for API access.
        dry_run: If True, don't actually call GitHub API.

    Returns:
        Dict with SUMMARIZER_TRIGGERED and related env vars.
    """
    if not issue_number:
        return {
            "SUMMARIZER_TRIGGERED": "false",
            "NON_BOT_COMMENT_COUNT": "0",
            "HAS_SUMMARY": "false",
        }

    comments = get_issue_comments(repo, issue_number, gh_token, dry_run=dry_run)

    non_bot_count = count_non_bot_comments(comments)
    has_summary = has_summary_comment(comments)

    should_trigger = non_bot_count >= min_comments and not has_summary

    result = {
        "SUMMARIZER_TRIGGERED": "true" if should_trigger else "false",
        "NON_BOT_COMMENT_COUNT": str(non_bot_count),
        "HAS_SUMMARY": "true" if has_summary else "false",
        "TOTAL_COMMENTS": str(len(comments)),
        "SUMMARIZER_MIN_COMMENTS": str(min_comments),
    }

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Check summarizer gate - determine if summarizer should trigger"
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=0,
        help="GitHub issue number",
    )
    parser.add_argument(
        "--min-comments",
        type=int,
        default=5,
        help="Minimum non-bot comments to trigger summarizer",
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

    result = check_summarizer_gate(
        issue_number=args.issue_number,
        min_comments=args.min_comments,
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
