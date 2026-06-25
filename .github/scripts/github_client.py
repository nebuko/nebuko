#!/usr/bin/env python3
"""
Shared GitHub API utilities for workflow scripts.

Provides subprocess-based wrappers around the GitHub CLI (gh) for:
- Fetching issue comments
- Posting comments
- Getting comment node IDs
- Minimizing comments

Supports --dry-run mode for testing without making actual API calls.

Usage:
    from github_client import get_issue_comments, post_comment
"""

import json
import os
import subprocess
import sys
from typing import List, Optional


def _gh_cli() -> List[str]:
    """Return gh CLI path."""
    return ["gh"]


def _run(
    args: List[str],
    dry_run: bool = False,
    input_data: Optional[str] = None,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run a gh CLI command and return the result."""
    if dry_run:
        print(f"[DRY RUN] Would run: gh {' '.join(args)}", file=sys.stderr)
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")
    return subprocess.run(_gh_cli() + args, capture_output=True, text=True, input=input_data, env=env)


def get_issue_comments(
    repo: str,
    issue_number: int,
    token: Optional[str] = None,
    dry_run: bool = False,
) -> List[dict]:
    """Fetch all comments from an issue via gh CLI.

    Args:
        repo: Repository in format owner/repo.
        issue_number: Issue number.
        token: GitHub token (falls back to GH_TOKEN env var).
        dry_run: If True, return empty list without making API call.

    Returns:
        List of comment dicts with 'body', 'user', 'id', etc.
    """
    if dry_run:
        return []

    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token

    result = _run(
        [
            "api",
            f"/repos/{repo}/issues/{issue_number}/comments",
            "--jq",
            ".",
        ],
        dry_run=dry_run,
        env=env,
    )

    if result.returncode != 0:
        print(
            f"Error fetching comments: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return []

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Failed to parse comments JSON: {e}", file=sys.stderr)
        return []


def get_comment_body(
    comment_id: str,
    repo: str,
    token: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[str]:
    """Fetch the body text of a GitHub issue comment by its numeric ID.

    Args:
        comment_id: The numeric comment ID.
        repo: Repository in format owner/repo.
        token: GitHub token.
        dry_run: If True, return None.

    Returns:
        Comment body text, or None on failure.
    """
    if dry_run:
        return None

    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token

    result = _run(
        [
            "api",
            f"/repos/{repo}/issues/comments/{comment_id}",
            "--jq",
            ".body",
        ],
        dry_run=dry_run,
        env=env,
    )

    if result.returncode != 0:
        print(f"Error fetching comment body: {result.stderr.strip()}", file=sys.stderr)
        return None

    body = result.stdout.strip()
    return body if body else None


def get_comment_node_id(
    comment_id: str,
    repo: str,
    token: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[str]:
    """Fetch GraphQL node_id for a given comment ID.

    Args:
        comment_id: The numeric or string comment ID.
        repo: Repository in format owner/repo.
        token: GitHub token.
        dry_run: If True, return None.

    Returns:
        GraphQL node_id string, or None on failure.
    """
    if dry_run:
        return None

    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token

    result = _run(
        [
            "api",
            f"/repos/{repo}/issues/comments/{comment_id}",
            "--jq",
            ".node_id",
        ],
        dry_run=dry_run,
        env=env,
    )

    if result.returncode != 0:
        print(f"Error fetching node ID: {result.stderr.strip()}", file=sys.stderr)
        return None

    node_id = result.stdout.strip()
    return node_id if node_id else None


def post_comment(
    repo: str,
    issue_number: int,
    body: str,
    token: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[dict]:
    """Post a comment to a GitHub issue.

    Args:
        repo: Repository in format owner/repo.
        issue_number: Issue number.
        body: Comment body text.
        token: GitHub token.
        dry_run: If True, skip the API call.

    Returns:
        Comment response dict, or None on failure.
    """
    if dry_run:
        print(f"[DRY RUN] Would post comment to {repo}#{issue_number}", file=sys.stderr)
        return None

    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token

    result = _run(
        [
            "api",
            f"/repos/{repo}/issues/{issue_number}/comments",
            "-f",
            f"body={body}",
        ],
        dry_run=dry_run,
        env=env,
    )

    if result.returncode != 0:
        print(f"Error posting comment: {result.stderr.strip()}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Failed to parse comment response: {e}", file=sys.stderr)
        return None


def minimize_comment(
    node_id: str,
    token: Optional[str] = None,
    dry_run: bool = False,
) -> bool:
    """Minimize a comment via GraphQL mutation.

    Args:
        node_id: GraphQL node_id of the comment to minimize.
        token: GitHub token.
        dry_run: If True, skip the API call.

    Returns:
        True if successful, False otherwise.
    """
    if dry_run:
        print(f"[DRY RUN] Would minimize comment {node_id}", file=sys.stderr)
        return True

    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token

    query = """
mutation($subjectId: ID!) {
  minimizeComment(input: {subjectId: $subjectId, classifier: OUTDATED}) {
    minimizedComment { minimizedReason }
  }
}
"""

    result = _run(
        [
            "api",
            "graphql",
            "-f",
            "query=" + query,
            "-f",
            f"subjectId={node_id}",
        ],
        dry_run=dry_run,
        env=env,
    )

    if result.returncode != 0:
        print(f"Error minimizing comment: {result.stderr.strip()}", file=sys.stderr)
        return False

    return True


def main():
    """CLI interface for testing individual functions."""
    import argparse

    parser = argparse.ArgumentParser(description="GitHub CLI client utilities")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # get-comments
    p_get = subparsers.add_parser("get-comments", help="Get issue comments")
    p_get.add_argument("--repo", required=True)
    p_get.add_argument("--issue-number", type=int, required=True)
    p_get.add_argument("--token")
    p_get.add_argument("--dry-run", action="store_true")

    # get-node-id
    p_node = subparsers.add_parser("get-node-id", help="Get comment node ID")
    p_node.add_argument("--comment-id", required=True)
    p_node.add_argument("--repo")
    p_node.add_argument("--token")
    p_node.add_argument("--dry-run", action="store_true")

    # get-body
    p_body = subparsers.add_parser("get-body", help="Get comment body text")
    p_body.add_argument("--comment-id", required=True)
    p_body.add_argument("--repo")
    p_body.add_argument("--token")
    p_body.add_argument("--dry-run", action="store_true")

    # post-comment
    p_post = subparsers.add_parser("post-comment", help="Post a comment")
    p_post.add_argument("--repo", required=True)
    p_post.add_argument("--issue-number", type=int, required=True)
    p_post.add_argument("--body", required=True)
    p_post.add_argument("--token")
    p_post.add_argument("--dry-run", action="store_true")

    # minimize
    p_min = subparsers.add_parser("minimize", help="Minimize a comment")
    p_min.add_argument("--node-id", required=True)
    p_min.add_argument("--token")
    p_min.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.command == "get-comments":
        comments = get_issue_comments(args.repo, args.issue_number, args.token, args.dry_run)
        print(json.dumps(comments, indent=2))
    elif args.command == "get-node-id":
        node_id = get_comment_node_id(args.comment_id, args.repo or "", args.token, args.dry_run)
        if node_id:
            print(node_id)
    elif args.command == "get-body":
        body = get_comment_body(args.comment_id, args.repo or "", args.token, args.dry_run)
        if body:
            print(body)
    elif args.command == "post-comment":
        result = post_comment(args.repo, args.issue_number, args.body, args.token, args.dry_run)
        if result:
            print(json.dumps(result, indent=2))
    elif args.command == "minimize":
        success = minimize_comment(args.node_id, args.token, args.dry_run)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
