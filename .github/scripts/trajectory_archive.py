#!/usr/bin/env python3
"""
Trajectory archive for Retrospective Harness Optimization (RHO).

Provides a Port/Adapter architecture for fetching and storing historical
agent trajectories from GitHub issues. Trajectories include issue metadata
and all embedded agent metrics from comments.

Port (interface): TrajectoryRepository
Adapter: GitHubTrajectoryFetcher (fetches from GitHub API)
Storage: Local JSON files in .rho/trajectories/
"""

import json
import os
import sys
import urllib.request
import urllib.error
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics


# ── Data Models ──────────────────────────────────────────────────────


@dataclass
class IssueMetadata:
    """Metadata about a GitHub issue used as a trajectory source."""

    issue_number: int = 0
    title: str = ""
    state: str = ""
    created_at: str = ""
    closed_at: str = ""
    labels: List[str] = field(default_factory=list)
    body: str = ""


@dataclass
class Trajectory:
    """
    A complete agent trajectory for a single issue.

    Contains the issue metadata and all agent metrics collected
    from the comments on that issue.
    """

    issue: IssueMetadata = field(default_factory=IssueMetadata)
    metrics: List[Metrics] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Serialize trajectory to a plain dictionary."""
        return {
            "issue": asdict(self.issue),
            "metrics": [m.to_dict() for m in self.metrics],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Trajectory":
        """Deserialize a trajectory from a dictionary."""
        issue_data = data.get("issue", {})
        metrics_data = data.get("metrics", [])
        return cls(
            issue=IssueMetadata(**{
                k: v for k, v in issue_data.items()
                if k in IssueMetadata.__dataclass_fields__
            }),
            metrics=[Metrics.from_dict(m) for m in metrics_data],
        )

    def to_json(self, indent: int = 2) -> str:
        """Serialize trajectory to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "Trajectory":
        """Deserialize a trajectory from a JSON string."""
        return cls.from_dict(json.loads(json_str))


# ── Port (Interface) ─────────────────────────────────────────────────


class TrajectoryRepository:
    """
    Port for trajectory storage and retrieval.

    This class handles local file-based persistence of trajectories
    in .rho/trajectories/. It provides a consistent interface regardless
    of where the trajectories originated (GitHub API, local files, etc.).
    """

    def __init__(self, base_dir: str = ".rho/trajectories"):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _filepath(self, issue_number: int) -> str:
        """Get the file path for a given issue number."""
        return os.path.join(self.base_dir, f"issue-{issue_number}.json")

    def save(self, trajectory: Trajectory) -> str:
        """
        Save a trajectory to disk.

        Args:
            trajectory: The trajectory to save.

        Returns:
            The file path where the trajectory was saved.
        """
        filepath = self._filepath(trajectory.issue.issue_number)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(trajectory.to_json())
        return filepath

    def load(self, issue_number: int) -> Optional[Trajectory]:
        """
        Load a trajectory by issue number.

        Args:
            issue_number: The GitHub issue number.

        Returns:
            The trajectory if found, None otherwise.
        """
        filepath = self._filepath(issue_number)
        if not os.path.isfile(filepath):
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            return Trajectory.from_json(f.read())

    def list_all(self) -> List[Trajectory]:
        """
        List all stored trajectories.

        Returns:
            List of all trajectories, sorted by issue number (descending).
        """
        trajectories = []
        if not os.path.isdir(self.base_dir):
            return trajectories
        for filename in os.listdir(self.base_dir):
            if filename.startswith("issue-") and filename.endswith(".json"):
                filepath = os.path.join(self.base_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        trajectories.append(Trajectory.from_json(f.read()))
                except (json.JSONDecodeError, KeyError):
                    continue
        trajectories.sort(key=lambda t: t.issue.issue_number, reverse=True)
        return trajectories

    def count(self) -> int:
        """Return the number of stored trajectories."""
        return len(self.list_all())


# ── Adapter (GitHub API) ─────────────────────────────────────────────


METRICS_REGEX_COMPILED = __import__("re").compile(r"<!--\s*agent-metrics:\s*(.*?)\s*-->")


def _extract_metrics_from_body(body: str) -> List[Metrics]:
    """
    Extract all Metrics objects from a comment body.

    Args:
        body: The comment body string.

    Returns:
        List of parsed Metrics objects.
    """
    metrics_list = []
    for match in METRICS_REGEX_COMPILED.finditer(body):
        try:
            metrics_list.append(Metrics.from_json(match.group(1)))
        except json.JSONDecodeError:
            continue
    return metrics_list


def _gh_get(url: str, token: str) -> bytes:
    """
    Perform a GET request to the GitHub API.

    Args:
        url: The API URL.
        token: GitHub personal access token.

    Returns:
        Response body bytes.

    Raises:
        urllib.error.HTTPError: On HTTP errors.
    """
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "git-flow-ai-rho")

    with urllib.request.urlopen(req) as response:
        return response.read()


def fetch_closed_issues(repo: str, token: str, days: int = 90, per_page: int = 100) -> List[Dict]:
    """
    Fetch closed issues from a GitHub repository.

    Args:
        repo: Repository in format owner/repo.
        token: GitHub personal access token.
        days: Only fetch issues closed within this many days.
        per_page: Number of issues per API page.

    Returns:
        List of issue dictionaries from the GitHub API.
    """
    since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    from datetime import timedelta
    since -= timedelta(days=days)
    since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_issues = []
    page = 1

    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=closed&per_page={per_page}&page={page}&since={since_str}"
        )
        try:
            data = _gh_get(url, token)
        except urllib.error.HTTPError as e:
            print(f"HTTP error fetching issues (page {page}): {e.code} - {e.reason}")
            break
        except Exception as e:
            print(f"Error fetching issues (page {page}): {e}")
            break

        issues = json.loads(data)
        if not issues:
            break
        all_issues.extend(issues)
        page += 1

    return all_issues


def fetch_issue_comments(repo: str, issue_number: int, token: str, per_page: int = 100) -> List[Dict]:
    """
    Fetch all comments from a single issue.

    Args:
        repo: Repository in format owner/repo.
        issue_number: The issue number.
        token: GitHub personal access token.
        per_page: Number of comments per API page.

    Returns:
        List of comment dictionaries from the GitHub API.
    """
    all_comments = []
    page = 1

    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
            f"?per_page={per_page}&page={page}"
        )
        try:
            data = _gh_get(url, token)
        except urllib.error.HTTPError as e:
            print(f"HTTP error fetching comments for #{issue_number} (page {page}): {e.code}")
            break
        except Exception as e:
            print(f"Error fetching comments for #{issue_number} (page {page}): {e}")
            break

        comments = json.loads(data)
        if not comments:
            break
        all_comments.extend(comments)
        page += 1

    return all_comments


def build_trajectory(issue: Dict, comments: List[Dict]) -> Trajectory:
    """
    Build a Trajectory from a GitHub issue and its comments.

    Args:
        issue: Issue dictionary from GitHub API.
        comments: List of comment dictionaries from GitHub API.

    Returns:
        A Trajectory object containing issue metadata and all extracted metrics.
    """
    issue_meta = IssueMetadata(
        issue_number=issue.get("number", 0),
        title=issue.get("title", ""),
        state=issue.get("state", ""),
        created_at=issue.get("created_at", ""),
        closed_at=issue.get("closed_at", ""),
        labels=[label.get("name", "") for label in issue.get("labels", [])],
        body=issue.get("body", "") or "",
    )

    all_metrics = []
    for comment in comments:
        body = comment.get("body", "")
        if body:
            all_metrics.extend(_extract_metrics_from_body(body))

    return Trajectory(issue=issue_meta, metrics=all_metrics)


class GitHubTrajectoryFetcher:
    """
    Adapter for fetching trajectories from the GitHub API.

    Combines issue listing, comment fetching, and metric extraction
    into a single interface for populating the trajectory archive.
    """

    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.token = token

    def fetch_recent(self, days: int = 90) -> List[Trajectory]:
        """
        Fetch all closed issues from the last N days and build trajectories.

        Only returns trajectories that have at least one metric entry
        (i.e., issues where the agent actually ran).

        Args:
            days: Look back this many days for closed issues.

        Returns:
            List of Trajectory objects with metrics.
        """
        issues = fetch_closed_issues(self.repo, self.token, days=days)
        trajectories = []

        for issue in issues:
            # Skip pull requests (they have a pull_request key)
            if "pull_request" in issue:
                continue

            comments = fetch_issue_comments(
                self.repo, issue["number"], self.token
            )
            trajectory = build_trajectory(issue, comments)

            # Only keep trajectories with at least one metric
            if trajectory.metrics:
                trajectories.append(trajectory)

        return trajectories

    def archive(self, repository: TrajectoryRepository, days: int = 90) -> int:
        """
        Fetch trajectories and save them to the local repository.

        Args:
            repository: Local TrajectoryRepository to save to.
            days: Look back this many days for closed issues.

        Returns:
            Number of trajectories saved (including updates).
        """
        trajectories = self.fetch_recent(days=days)
        count = 0
        for trajectory in trajectories:
            repository.save(trajectory)
            count += 1
        return count


def main():
    """CLI entry point: fetch and archive trajectories."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    days = int(os.environ.get("RHO_DAYS", "90"))

    if not token or not repo:
        print("Error: GITHUB_TOKEN and GITHUB_REPOSITORY env vars are required.")
        sys.exit(1)

    print(f"Fetching trajectories for {repo} (last {days} days)...")

    repository = TrajectoryRepository()
    fetcher = GitHubTrajectoryFetcher(repo, token)

    count = fetcher.archive(repository, days=days)
    total = repository.count()

    print(f"Saved {count} trajectories. Total archived: {total}")


if __name__ == "__main__":
    main()
