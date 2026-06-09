#!/usr/bin/env python3
"""
Trajectory Archive — fetch closed issues and extract agent metrics for RHO.

Reads closed GitHub issues, extracts embedded agent metrics from comments,
and persists structured trajectory data to `.rho/trajectories/` for later
coreset selection and rollout analysis.

Environment Variables:
    GITHUB_TOKEN: GitHub API token (required)
    GITHUB_REPOSITORY: Repository in format owner/repo (required)

Usage:
    from trajectory_archive import fetch_trajectories, load_archive

    # Fetch and store trajectories from closed issues
    trajectories = fetch_trajectories(repo, token, max_issues=50)

    # Load previously stored trajectories
    trajectories = load_archive(".rho/trajectories")
"""

import json
import os
import sys
import urllib.request
import urllib.error
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics
from summarize_metrics import extract_metrics_from_comment


@dataclass
class Trajectory:
    """A single issue trajectory with full context and agent metrics."""

    issue_number: int = 0
    issue_title: str = ""
    issue_body: str = ""
    issue_closed_at: str = ""
    issue_state: str = "closed"
    comments: List[Dict[str, Any]] = field(default_factory=list)
    metrics: List[Metrics] = field(default_factory=list)
    # Aggregated difficulty indicators (computed from metrics)
    avg_error_rate: float = 0.0
    total_fix_requests: int = 0
    total_tool_calls: int = 0
    avg_runtime_ms: float = 0.0
    total_tokens: int = 0


def fetch_closed_issues(repo: str, token: str, per_page: int = 100,
                        max_issues: int = 100) -> List[Dict[str, Any]]:
    """Fetch closed issues from a GitHub repository.

    Args:
        repo: Repository in format owner/repo.
        token: GitHub API token.
        per_page: Items per API page.
        max_issues: Maximum issues to fetch.

    Returns:
        List of issue dicts from GitHub API.
    """
    all_issues = []
    page = 1

    while len(all_issues) < max_issues:
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=closed&per_page={per_page}&page={page}"
        )
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

        # Skip pull requests
        issues = [i for i in issues if "pull_request" not in i]
        all_issues.extend(issues[:max_issues - len(all_issues)])
        page += 1

    return all_issues[:max_issues]


def fetch_issue_comments(repo: str, issue_number: int, token: str,
                         per_page: int = 100) -> List[Dict[str, Any]]:
    """Fetch all comments from a single issue.

    Reuses pagination pattern from summarize_metrics.py.

    Args:
        repo: Repository in format owner/repo.
        issue_number: Issue number.
        token: GitHub API token.
        per_page: Items per API page.

    Returns:
        List of comment dicts.
    """
    all_comments = []
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
            print(f"HTTP error fetching comments #{issue_number}: {e.code}")
            break
        except Exception as e:
            print(f"Error fetching comments #{issue_number}: {e}")
            break

        if not comments:
            break

        all_comments.extend(comments)
        page += 1

    return all_comments


def build_trajectory(issue: Dict[str, Any],
                     comments: List[Dict[str, Any]]) -> Trajectory:
    """Build a Trajectory object from issue data and comments.

    Extracts embedded metrics from all comments and computes aggregate
    difficulty indicators.

    Args:
        issue: GitHub issue dict.
        comments: List of comment dicts for this issue.

    Returns:
        Trajectory with extracted metrics and computed indicators.
    """
    # Extract metrics from all comments
    all_metrics: List[Metrics] = []
    for comment in comments:
        body = comment.get("body", "")
        if body:
            all_metrics.extend(extract_metrics_from_comment(body))

    if not all_metrics:
        # Still create trajectory but with no metrics — skip for coreset
        return Trajectory(
            issue_number=issue.get("number", 0),
            issue_title=issue.get("title", ""),
            issue_body=issue.get("body", "") or "",
            issue_closed_at=issue.get("closed_at", ""),
        )

    # Compute aggregate difficulty indicators
    error_rates = [m.tool_call_error_rate for m in all_metrics]
    avg_error_rate = sum(error_rates) / len(error_rates) if error_rates else 0.0

    total_fix_requests = sum(m.review_fix_requests for m in all_metrics)
    total_tool_calls = sum(m.nb_tool_calls for m in all_metrics)

    runtimes = [m.agent_runtime_ms for m in all_metrics if m.agent_runtime_ms > 0]
    avg_runtime_ms = sum(runtimes) / len(runtimes) if runtimes else 0.0

    total_tokens = sum(m.input_tokens + m.output_tokens for m in all_metrics)

    # Store minimal comment metadata (author, body snippet, created_at)
    comment_summaries = []
    for c in comments:
        body = c.get("body", "")
        comment_summaries.append({
            "author": c.get("user", {}).get("login", ""),
            "created_at": c.get("created_at", ""),
            "body_preview": body[:500] if body else "",
            "has_metrics": bool(extract_metrics_from_comment(body)),
        })

    return Trajectory(
        issue_number=issue.get("number", 0),
        issue_title=issue.get("title", ""),
        issue_body=issue.get("body", "") or "",
        issue_closed_at=issue.get("closed_at", ""),
        issue_state=issue.get("state", "closed"),
        comments=comment_summaries,
        metrics=all_metrics,
        avg_error_rate=avg_error_rate,
        total_fix_requests=total_fix_requests,
        total_tool_calls=total_tool_calls,
        avg_runtime_ms=avg_runtime_ms,
        total_tokens=total_tokens,
    )


def trajectory_to_dict(traj: Trajectory) -> Dict[str, Any]:
    """Serialize a Trajectory to a plain dict for JSON storage."""
    d = asdict(traj)
    # Convert Metrics objects to dicts
    d["metrics"] = [m.to_dict() if isinstance(m, Metrics) else m for m in d["metrics"]]
    return d


def trajectory_from_dict(data: Dict[str, Any]) -> Trajectory:
    """Deserialize a Trajectory from a dict (JSON load)."""
    traj = Trajectory(**{k: v for k, v in data.items() if k in Trajectory.__dataclass_fields__})
    # Restore Metrics objects
    traj.metrics = [
        Metrics.from_dict(m) if isinstance(m, dict) else m
        for m in traj.metrics
    ]
    return traj


def ensure_archive_dir(archive_dir: str) -> str:
    """Create archive directory if it doesn't exist."""
    os.makedirs(archive_dir, exist_ok=True)
    return archive_dir


def save_trajectories(trajectories: List[Trajectory],
                      archive_dir: str = ".rho/trajectories") -> List[str]:
    """Save trajectories as individual JSON files.

    Args:
        trajectories: List of Trajectory objects.
        archive_dir: Directory to store trajectory files.

    Returns:
        List of file paths that were written.
    """
    ensure_archive_dir(archive_dir)
    paths = []

    for traj in trajectories:
        filename = f"issue_{traj.issue_number}.json"
        filepath = os.path.join(archive_dir, filename)
        with open(filepath, "w") as f:
            json.dump(trajectory_to_dict(traj), f, indent=2)
        paths.append(filepath)

    # Also save an index file with metadata for all trajectories
    index_path = os.path.join(archive_dir, "index.json")
    index = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(trajectories),
        "trajectories": [
            {
                "issue_number": t.issue_number,
                "title": t.issue_title,
                "metrics_count": len(t.metrics),
                "avg_error_rate": t.avg_error_rate,
                "total_fix_requests": t.total_fix_requests,
            }
            for t in trajectories
        ],
    }
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    paths.append(index_path)

    return paths


def load_archive(archive_dir: str = ".rho/trajectories") -> List[Trajectory]:
    """Load all trajectories from archive directory.

    Args:
        archive_dir: Directory containing trajectory JSON files.

    Returns:
        List of Trajectory objects.
    """
    trajectories = []

    if not os.path.isdir(archive_dir):
        return trajectories

    for filename in sorted(os.listdir(archive_dir)):
        if not filename.startswith("issue_") or not filename.endswith(".json"):
            continue

        filepath = os.path.join(archive_dir, filename)
        try:
            with open(filepath) as f:
                data = json.load(f)
            trajectories.append(trajectory_from_dict(data))
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to load {filename}: {e}")

    return trajectories


def fetch_trajectories(repo: str, token: str, max_issues: int = 100,
                       archive_dir: str = ".rho/trajectories") -> List[Trajectory]:
    """Fetch closed issues, extract metrics, and persist to archive.

    Full pipeline: fetch issues → fetch comments → extract metrics → save.

    Args:
        repo: Repository in format owner/repo.
        token: GitHub API token.
        max_issues: Maximum issues to process.
        archive_dir: Directory to persist trajectories.

    Returns:
        List of Trajectory objects.
    """
    print(f"Fetching closed issues from {repo} (max {max_issues})...")
    issues = fetch_closed_issues(repo, token, max_issues=max_issues)
    print(f"Fetched {len(issues)} closed issues (excluding PRs).")

    trajectories = []
    for i, issue in enumerate(issues):
        issue_num = issue.get("number", 0)
        print(f"[{i + 1}/{len(issues)}] Processing issue #{issue_num}...")

        comments = fetch_issue_comments(repo, issue_num, token)
        traj = build_trajectory(issue, comments)
        trajectories.append(traj)

    # Save to archive
    has_metrics = [t for t in trajectories if t.metrics]
    print(f"Found {len(has_metrics)} issues with agent metrics out of {len(trajectories)}.")

    if has_metrics:
        save_trajectories(trajectories, archive_dir)
        print(f"Saved {len(trajectories)} trajectories to {archive_dir}/")

    return trajectories


def main():
    """CLI entry point for trajectory archival."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    max_issues = int(os.environ.get("RHO_MAX_ISSUES", "100"))

    if not token or not repo:
        print("Error: GITHUB_TOKEN and GITHUB_REPOSITORY env vars are required.")
        sys.exit(1)

    trajectories = fetch_trajectories(repo, token, max_issues=max_issues)
    print(f"Done. Archived {len(trajectories)} trajectories.")


if __name__ == "__main__":
    main()
