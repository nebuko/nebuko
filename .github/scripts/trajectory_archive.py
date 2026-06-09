#!/usr/bin/env python3
"""
Trajectory Archive — fetch closed issues and extract agent trajectories.

Reads closed issues via the GitHub API, extracts embedded metrics from
issue comments, and stores structured trajectory data in .rho/trajectories/.

Each trajectory is a JSON file named by issue number containing:
- Issue metadata (title, body, labels, state, closed_at)
- All extracted Metrics objects from issue comments
- Computed difficulty scores

Usage:
    python trajectory_archive.py [--since YYYY-MM-DD] [--limit N]

Environment Variables:
    GITHUB_TOKEN: GitHub API token (required)
    GITHUB_REPOSITORY: Repository in format owner/repo (required)
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics

ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", ".rho", "trajectories")
METRICS_REGEX = re.compile(r"<!--\s*agent-metrics:\s*(.*?)\s*-->")


def fetch_closed_issues(repo: str, token: str, since: Optional[str] = None,
                        limit: Optional[int] = None, per_page: int = 100) -> List[Dict[str, Any]]:
    """Fetch closed issues from a repository via the GitHub API.

    Args:
        repo: Repository in format owner/repo.
        token: GitHub API token.
        since: Optional ISO date string to filter issues closed after this date.
        limit: Optional maximum number of issues to fetch.
        per_page: Items per API page request.

    Returns:
        List of issue dictionaries.
    """
    all_issues: List[Dict[str, Any]] = []
    page = 1
    url_base = f"https://api.github.com/repos/{repo}/issues"
    state_filter = "state=closed&labels="  # exclude PRs via label filtering

    while True:
        qs_parts = [f"{state_filter}", f"per_page={per_page}", f"page={page}", "sort=updated&direction=desc"]
        if since:
            qs_parts.append(f"since={since}")
        query_string = "&".join(qs_parts)
        url = f"{url_base}?{query_string}"

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "git-flow-ai-rho-archive")

        try:
            with urllib.request.urlopen(req) as response:
                issues = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"HTTP error fetching issues (page {page}): {e.code} - {e.reason}")
            break
        except Exception as e:
            print(f"Error fetching issues (page {page}): {e}")
            break

        if not issues:
            break

        # Filter out pull requests
        for issue in issues:
            if "pull_request" not in issue:
                all_issues.append(issue)

        # Apply since filter on closed_at
        if since:
            all_issues = [
                i for i in all_issues
                if i.get("closed_at") and i["closed_at"] >= since
            ]

        if limit and len(all_issues) >= limit:
            all_issues = all_issues[:limit]
            break

        page += 1

        # Stop if we haven't gotten a full page
        if len(issues) < per_page:
            break

    return all_issues


def fetch_issue_comments(repo: str, issue_number: int, token: str,
                         per_page: int = 100) -> List[Dict[str, Any]]:
    """Fetch all comments from a single issue, handling pagination.

    Args:
        repo: Repository in format owner/repo.
        issue_number: Issue number.
        token: GitHub API token.
        per_page: Items per API page request.

    Returns:
        List of comment dictionaries.
    """
    all_comments: List[Dict[str, Any]] = []
    page = 1

    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
            f"?per_page={per_page}&page={page}"
        )
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "git-flow-ai-rho-archive")

        try:
            with urllib.request.urlopen(req) as response:
                comments = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"HTTP error fetching comments (page {page}): {e.code} - {e.reason}")
            break
        except Exception as e:
            print(f"Error fetching comments (page {page}): {e}")
            break

        if not comments:
            break

        all_comments.extend(comments)
        page += 1

    return all_comments


def extract_metrics_from_comment(body: str) -> List[Metrics]:
    """Extract all Metrics objects from a comment body.

    Args:
        body: Raw comment body string.

    Returns:
        List of Metrics objects parsed from HTML comment blocks.
    """
    metrics_list: List[Metrics] = []
    for match in METRICS_REGEX.finditer(body):
        try:
            metrics = Metrics.from_json(match.group(1))
            metrics_list.append(metrics)
        except json.JSONDecodeError:
            pass
    return metrics_list


def build_trajectory(issue: Dict[str, Any], comments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a trajectory record from an issue and its comments.

    Args:
        issue: Issue dictionary from GitHub API.
        comments: List of comment dictionaries.

    Returns:
        Trajectory dictionary with issue metadata and extracted metrics.
    """
    all_metrics: List[Metrics] = []
    for comment in comments:
        body = comment.get("body", "")
        if body:
            all_metrics.extend(extract_metrics_from_comment(body))

    trajectory: Dict[str, Any] = {
        "issue_number": issue["number"],
        "title": issue.get("title", ""),
        "body": issue.get("body", ""),
        "labels": [lb["name"] for lb in issue.get("labels", [])],
        "state": issue.get("state", ""),
        "closed_at": issue.get("closed_at", ""),
        "created_at": issue.get("created_at", ""),
        "updated_at": issue.get("updated_at", ""),
        "metrics": [m.to_dict() for m in all_metrics],
        "metrics_count": len(all_metrics),
    }

    # Compute quick difficulty heuristic for sorting
    error_rates = [m.tool_call_error_rate for m in all_metrics if m.tool_call_error_rate > 0]
    fix_requests = sum(m.review_fix_requests for m in all_metrics)
    tool_calls = sum(m.nb_tool_calls for m in all_metrics)
    runtimes = [m.agent_runtime_ms for m in all_metrics if m.agent_runtime_ms > 0]

    trajectory["quick_difficulty"] = {
        "max_error_rate": max(error_rates) if error_rates else 0.0,
        "total_fix_requests": fix_requests,
        "total_tool_calls": tool_calls,
        "avg_runtime_ms": (sum(runtimes) / len(runtimes)) if runtimes else 0.0,
    }

    return trajectory


def archive_trajectories(issues: List[Dict[str, Any]], repo: str,
                         token: str) -> List[Dict[str, Any]]:
    """Archive trajectories for a list of issues.

    Fetches comments, builds trajectory records, and writes JSON files
    to the archive directory. Skips issues that already have archived
    trajectories.

    Args:
        issues: List of issue dictionaries.
        repo: Repository in format owner/repo.
        token: GitHub API token.

    Returns:
        List of trajectory records (both newly archived and existing).
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    all_trajectories: List[Dict[str, Any]] = []

    for issue in issues:
        issue_num = issue["number"]
        filepath = os.path.join(ARCHIVE_DIR, f"issue-{issue_num}.json")

        # Skip if already archived (use cached version)
        if os.path.exists(filepath):
            with open(filepath) as f:
                traj = json.load(f)
            all_trajectories.append(traj)
            continue

        comments = fetch_issue_comments(repo, issue_num, token)
        traj = build_trajectory(issue, comments)

        if traj["metrics_count"] > 0:
            with open(filepath, "w") as f:
                json.dump(traj, f, indent=2)
            all_trajectories.append(traj)

    return all_trajectories


def load_archived_trajectories() -> List[Dict[str, Any]]:
    """Load all archived trajectories from disk.

    Returns:
        List of trajectory dictionaries sorted by issue number descending.
    """
    if not os.path.isdir(ARCHIVE_DIR):
        return []

    trajectories: List[Dict[str, Any]] = []
    for fname in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
        if fname.endswith(".json"):
            filepath = os.path.join(ARCHIVE_DIR, fname)
            with open(filepath) as f:
                traj = json.load(f)
            trajectories.append(traj)

    return trajectories


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print("Error: GITHUB_TOKEN and GITHUB_REPOSITORY env vars are required.")
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="Archive agent trajectories from closed issues")
    parser.add_argument("--since", type=str, default=None, help="Only issues closed after this date (ISO)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of issues to fetch")
    args = parser.parse_args()

    print(f"Fetching closed issues for {repo}...")
    issues = fetch_closed_issues(repo, token, since=args.since, limit=args.limit)
    print(f"Fetched {len(issues)} closed issues.")

    trajectories = archive_trajectories(issues, repo, token)
    print(f"Archived {len(trajectories)} trajectories with metrics.")

    # Print summary
    total_metrics = sum(t["metrics_count"] for t in trajectories)
    print(f"Total metric entries across all trajectories: {total_metrics}")


if __name__ == "__main__":
    main()
