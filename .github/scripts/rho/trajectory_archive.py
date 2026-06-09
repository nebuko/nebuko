#!/usr/bin/env python3
"""
Trajectory archive — fetch closed issues and store historical trajectories.

Reads metrics from GitHub issue comments and persists them locally for
RHO coreset selection.  Uses the same extraction logic as
summarize_metrics.py so the data is consistent.

Port/Adapter design:
    GitHubAPIClient  —  Adapter (HTTP calls to GitHub)
    TrajectoryArchive —  Core logic (orchestrates fetch + storage)
"""

import json
import os
import sys
import urllib.request
import urllib.error
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(__file__) + "/..")

from metrics_schema import Metrics
from summarize_metrics import extract_metrics_from_comment


# ── Data Models ───────────────────────────────────────────────────────

@dataclass
class Trajectory:
    """A single historical task trajectory."""

    issue_number: int
    issue_title: str
    issue_body: str
    closed_at: str
    metrics: List[Metrics] = field(default_factory=list)
    outcome: Optional[str] = None  # e.g. "merged", "closed", "failed"


# ── GitHub API Adapter (Port) ─────────────────────────────────────────

class GitHubAPIClient:
    """
    Thin adapter for GitHub REST API.

    All methods use urllib only (no external deps).  Designed to be
    easily mockable in tests.
    """

    def __init__(self, token: str, repo: str):
        self._token = token
        self._repo = repo
        self._base = f"https://api.github.com/repos/{repo}"

    def _request(self, url: str) -> dict | list:
        """Execute a GET request and return parsed JSON."""
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"token {self._token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "git-flow-ai-rho")

        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list_closed_issues(self, state="closed", per_page=100, max_pages=5) -> List[dict]:
        """List closed issues, handling pagination."""
        all_issues: list = []
        for page in range(1, max_pages + 1):
            url = f"{self._base}/issues?state={state}&per_page={per_page}&page={page}"
            issues = self._request(url)
            if not issues:
                break
            all_issues.extend(issues)
        return all_issues

    def get_issue(self, issue_number: int) -> dict:
        """Get a single issue by number."""
        return self._request(f"{self._base}/issues/{issue_number}")

    def get_issue_comments(self, issue_number: int, per_page=100) -> List[dict]:
        """Get all comments for an issue."""
        all_comments: list = []
        page = 1
        while True:
            url = f"{self._base}/issues/{issue_number}/comments?per_page={per_page}&page={page}"
            comments = self._request(url)
            if not comments:
                break
            all_comments.extend(comments)
            page += 1
        return all_comments


# ── Core Archive Logic ────────────────────────────────────────────────

def _filter_agent_issues(issues: List[dict]) -> List[dict]:
    """Filter out PRs and non-agent issues."""
    result = []
    for issue in issues:
        # Skip pull requests
        if issue.get("pull_request"):
            continue
        # Only keep issues that had agent activity
        body = (issue.get("body") or "").lower()
        if any(marker in body for marker in ["@@genius", "@@dev", "@@ctx", "@@review", "<agentrunconfig>"]):
            result.append(issue)
    return result


def _build_trajectory(issue: dict, comments: List[dict]) -> Optional[Trajectory]:
    """Build a Trajectory from an issue and its comments."""
    all_metrics: list[Metrics] = []

    # Extract metrics from issue body
    body = issue.get("body", "")
    all_metrics.extend(extract_metrics_from_comment(body))

    # Extract metrics from all comments
    for comment in comments:
        comment_body = comment.get("body", "")
        all_metrics.extend(extract_metrics_from_comment(comment_body))

    if not all_metrics:
        return None

    return Trajectory(
        issue_number=issue["number"],
        issue_title=issue.get("title", ""),
        issue_body=issue.get("body", ""),
        closed_at=issue.get("closed_at", datetime.now(timezone.utc).isoformat()),
        metrics=all_metrics,
        outcome=issue.get("state", "closed"),
    )


def _ensure_archive_dir(archive_dir: str) -> Path:
    """Create archive directory if it doesn't exist."""
    path = Path(archive_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_trajectory(trajectory: Trajectory, archive_dir: str) -> str:
    """
    Save a single trajectory as a JSON file.

    Returns the file path.
    """
    archive_path = _ensure_archive_dir(archive_dir)
    filename = f"issue-{trajectory.issue_number}.json"
    filepath = archive_path / filename

    data = {
        "issue_number": trajectory.issue_number,
        "issue_title": trajectory.issue_title,
        "issue_body": trajectory.issue_body,
        "closed_at": trajectory.closed_at,
        "outcome": trajectory.outcome,
        "metrics": [m.to_dict() for m in trajectory.metrics],
    }

    filepath.write_text(json.dumps(data, indent=2))
    return str(filepath)


def load_trajectory(archive_dir: str, issue_number: int) -> Optional[Trajectory]:
    """Load a trajectory from the archive by issue number."""
    filepath = Path(archive_dir) / f"issue-{issue_number}.json"
    if not filepath.exists():
        return None

    data = json.loads(filepath.read_text())
    return Trajectory(
        issue_number=data["issue_number"],
        issue_title=data["issue_title"],
        issue_body=data["issue_body"],
        closed_at=data["closed_at"],
        metrics=[Metrics.from_dict(m) for m in data["metrics"]],
        outcome=data.get("outcome"),
    )


def list_trajectories(archive_dir: str) -> List[Trajectory]:
    """List all trajectories in the archive directory."""
    archive_path = Path(archive_dir)
    if not archive_path.exists():
        return []

    trajectories = []
    for filepath in sorted(archive_path.glob("issue-*.json")):
        data = json.loads(filepath.read_text())
        trajectories.append(Trajectory(
            issue_number=data["issue_number"],
            issue_title=data["issue_title"],
            issue_body=data["issue_body"],
            closed_at=data["closed_at"],
            metrics=[Metrics.from_dict(m) for m in data["metrics"]],
            outcome=data.get("outcome"),
        ))
    return trajectories


class TrajectoryArchive:
    """
    Orchestrates fetching, filtering, and storing trajectories.

    Depends on GitHubAPIClient (adapter) — making it easy to mock in tests.
    """

    def __init__(self, client: GitHubAPIClient, archive_dir: str = ".rho/trajectories"):
        self.client = client
        self.archive_dir = archive_dir

    def fetch_and_store(self, max_issues: int = 50, max_age_days: int = 90) -> List[Trajectory]:
        """
        Fetch closed issues, extract trajectories, and store them.

        Args:
            max_issues: Maximum number of closed issues to scan.
            max_age_days: Only consider issues closed within this window (0 = no limit).

        Returns:
            List of stored Trajectory objects.
        """
        issues = self.client.list_closed_issues(per_page=100, max_pages=5)
        issues = _filter_agent_issues(issues[:max_issues])

        stored: list[Trajectory] = []
        now = datetime.now(timezone.utc)

        for issue in issues:
            # Age filter
            if max_age_days > 0 and issue.get("closed_at"):
                closed_at = datetime.fromisoformat(issue["closed_at"].replace("Z", "+00:00"))
                age_days = (now - closed_at).days
                if age_days > max_age_days:
                    continue

            comments = self.client.get_issue_comments(issue["number"])
            trajectory = _build_trajectory(issue, comments)

            if trajectory:
                save_trajectory(trajectory, self.archive_dir)
                stored.append(trajectory)

        return stored


def main():
    """CLI entry point for fetching and storing trajectories."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    archive_dir = os.environ.get("RHO_ARCHIVE_DIR", ".rho/trajectories")

    if not token or not repo:
        print("Error: GITHUB_TOKEN and GITHUB_REPOSITORY env vars are required.")
        sys.exit(1)

    client = GitHubAPIClient(token=token, repo=repo)
    archive = TrajectoryArchive(client=client, archive_dir=archive_dir)

    max_age = int(os.environ.get("RHO_MAX_AGE_DAYS", "90"))
    max_issues = int(os.environ.get("RHO_MAX_ISSUES", "50"))

    trajectories = archive.fetch_and_store(max_issues=max_issues, max_age_days=max_age)
    print(f"Stored {len(trajectories)} trajectories in {archive_dir}")


if __name__ == "__main__":
    main()
