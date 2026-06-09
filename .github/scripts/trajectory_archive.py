#!/usr/bin/env python3
"""
Trajectory Archive — Historical trajectory persistence for RHO.

Fetches closed GitHub issues, extracts embedded agent metrics from comments,
and stores trajectory records as JSON files. Provides load/save/list/prune
utilities for downstream coreset selection.

Uses only Python standard library plus yaml. Reuses METRICS_REGEX and
extract_metrics_from_comment from summarize_metrics.py.

Environment Variables:
    GITHUB_TOKEN: GitHub API token (required for GitHub operations)
    GITHUB_REPOSITORY: Repository in format owner/repo (required)

Usage:
    python trajectory_archive.py fetch    # Fetch and archive new trajectories
    python trajectory_archive.py list     # List archived trajectories
    python trajectory_archive.py prune    # Prune old archives
"""

import json
import os
import sys
import urllib.request
import urllib.error
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics
from summarize_metrics import METRICS_REGEX, extract_metrics_from_comment


@dataclass
class IssueMetadata:
    """Structural metadata extracted from a GitHub issue."""

    issue_number: int = 0
    title: str = ""
    body: str = ""
    state: str = ""
    labels: List[str] = field(default_factory=list)
    closed_at: str = ""
    html_url: str = ""
    author_login: str = ""


@dataclass
class TrajectoryRecord:
    """Complete trajectory record persisted to disk."""

    issue_number: int = 0
    metadata: Optional[Dict[str, Any]] = None
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    comments_count: int = 0
    archiving_date: str = ""
    outcome_score: float = 0.0


def fetch_closed_issues(repo: str, token: str, per_page: int = 100,
                        max_issues: int = 50, state: str = "closed") -> List[Dict]:
    """
    Fetch closed issues from GitHub Issues API (excluding PRs).

    Args:
        repo: Repository in format "owner/name".
        token: GitHub API token.
        per_page: Items per API page.
        max_issues: Maximum total issues to fetch.
        state: GitHub issue state filter.

    Returns:
        List of issue dictionaries from the GitHub API.
    """
    all_issues = []
    page = 1

    while len(all_issues) < max_issues:
        remaining = max_issues - len(all_issues)
        actual_per_page = min(per_page, remaining)

        url = (
            f"https://api.github.com/repos/{repo}/issues"
            f"?state={state}&per_page={actual_per_page}&page={page}"
            f"&labels=&sort=updated&direction=desc"
        )

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "git-flow-ai-rho-archive")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                items = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"[archive] HTTP error fetching issues (page {page}): {e.code} - {e.reason}")
            break
        except Exception as e:
            print(f"[archive] Error fetching issues (page {page}): {e}")
            break

        if not items:
            break

        # Filter out pull requests
        issues_only = [item for item in items if "pull_request" not in item]
        all_issues.extend(issues_only)

        if len(items) < per_page:
            break

        page += 1

    return all_issues[:max_issues]


def extract_issue_metadata(issue_dict: Dict[str, Any]) -> IssueMetadata:
    """Extract structural metadata from a GitHub issue dict.

    Args:
        issue_dict: Raw issue dictionary from GitHub API.

    Returns:
        IssueMetadata dataclass.
    """
    labels = [lb.get("name", "") for lb in issue_dict.get("labels", [])]
    return IssueMetadata(
        issue_number=issue_dict.get("number", 0),
        title=issue_dict.get("title", ""),
        body=issue_dict.get("body", "") or "",
        state=issue_dict.get("state", ""),
        labels=labels,
        closed_at=issue_dict.get("closed_at", "") or "",
        html_url=issue_dict.get("html_url", ""),
        author_login=(issue_dict.get("user") or {}).get("login", ""),
    )


def fetch_issue_comments(repo: str, issue_number: int, token: str,
                         per_page: int = 100) -> List[Dict]:
    """
    Fetch all comments from a single issue, handling pagination.

    Args:
        repo: Repository in format "owner/name".
        issue_number: Issue number.
        token: GitHub API token.
        per_page: Items per API page.

    Returns:
        List of comment dictionaries.
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
            with urllib.request.urlopen(req, timeout=30) as resp:
                comments = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"[archive] HTTP error for issue #{issue_number}: {e.code}")
            break
        except Exception as e:
            print(f"[archive] Error for issue #{issue_number}: {e}")
            break

        if not comments:
            break

        all_comments.extend(comments)
        page += 1

    return all_comments


def build_trajectory(issue_dict: Dict[str, Any], comments: List[Dict]) -> TrajectoryRecord:
    """Assemble a TrajectoryRecord from issue data and comments.

    Args:
        issue_dict: Raw issue dict from GitHub API.
        comments: List of comment dicts fetched for this issue.

    Returns:
        TrajectoryRecord with extracted metrics and metadata.
    """
    issue_num = issue_dict.get("number", 0)
    all_metrics: List[Metrics] = []

    # Extract metrics from issue body
    body = issue_dict.get("body", "")
    if body:
        all_metrics.extend(extract_metrics_from_comment(body))

    # Extract metrics from each comment
    for comment in comments:
        cb = comment.get("body", "")
        if cb:
            all_metrics.extend(extract_metrics_from_comment(cb))

    # Compute outcome score (lower is worse — more errors/fix requests means lower score)
    outcome_score = compute_outcome_score(all_metrics)

    return TrajectoryRecord(
        issue_number=issue_num,
        metadata=_metadata_to_dict(issue_dict),
        metrics=[m.to_dict() for m in all_metrics],
        comments_count=len(comments),
        archiving_date=datetime.now(timezone.utc).isoformat(),
        outcome_score=outcome_score,
    )


def compute_outcome_score(metrics_list: List[Metrics]) -> float:
    """Compute an outcome quality score from a list of Metrics objects.

    Higher score = better outcome.  Scale is roughly 0–100.

    Penalizes: high error rates, many review fix requests, excessive runtime.
    Rewards: low error rates, few fix requests, reasonable efficiency.

    Args:
        metrics_list: List of Metrics from one issue.

    Returns:
        Float score (clamped to 0–100).
    """
    if not metrics_list:
        return 50.0  # Neutral default for issues with no metrics

    total_errors = sum(m.tool_call_error_rate for m in metrics_list)
    total_fix_requests = sum(m.review_fix_requests for m in metrics_list)
    total_runtime = sum(m.agent_runtime_ms for m in metrics_list)
    n_runs = len(metrics_list)

    avg_error_rate = total_errors / n_runs
    avg_fix_requests = total_fix_requests / n_runs
    avg_runtime = total_runtime / n_runs

    # Start at 100, subtract penalties
    score = 100.0

    # Error penalty: 1 point per percent error rate (max 40)
    score -= min(avg_error_rate * 1.0, 40.0)

    # Fix request penalty: 5 points per fix request (max 30)
    score -= min(avg_fix_requests * 5.0, 30.0)

    # Runtime penalty: penalize very long runs (>120s averaged)
    if avg_runtime > 120000:
        excess_seconds = (avg_runtime - 120000) / 1000
        score -= min(excess_seconds * 0.1, 15.0)

    return max(0.0, min(100.0, score))


def _metadata_to_dict(issue_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Extract key metadata fields from an issue dict.

    Args:
        issue_dict: Raw issue dict from GitHub API.

    Returns:
        Simplified metadata dictionary suitable for archival.
    """
    meta_keys = {
        "number", "title", "state", "labels", "closed_at",
        "html_url", "created_at", "updated_at", "body", "author_association",
    }
    result = {}
    for key in meta_keys:
        if key in issue_dict:
            if key == "labels":
                result[key] = [lb.get("name", "") for lb in issue_dict.get("labels", [])]
            else:
                result[key] = issue_dict[key]
    return result


# ── Persistence ─────────────────────────────────────────────────────


def get_archive_dir(path_hint: str = ".rho/trajectories") -> Path:
    """Return the archive directory Path, creating if absent.

    Args:
        path_hint: Relative or absolute path to the archive dir.

    Returns:
        Path object for the archive directory.
    """
    archive_dir = Path(path_hint)
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir


def trajectory_filename(issue_number: int) -> str:
    """Generate the canonical filename for a trajectory record.

    Args:
        issue_number: GitHub issue number.

    Returns:
        Filename string.
    """
    return f"issue-{issue_number:04d}.json"


def save_trajectory(record: TrajectoryRecord, archive_dir: Path) -> Path:
    """Serialize a TrajectoryRecord to a JSON file.

    Args:
        record: TrajectoryRecord to persist.
        archive_dir: Target directory.

    Returns:
        Path to the saved file.
    """
    filepath = archive_dir / trajectory_filename(record.issue_number)
    data = {
        "issue_number": record.issue_number,
        "metadata": record.metadata,
        "metrics": record.metrics,
        "comments_count": record.comments_count,
        "archiving_date": record.archiving_date,
        "outcome_score": record.outcome_score,
    }
    filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return filepath


def load_trajectory(issue_number: int, archive_dir: Path) -> Optional[TrajectoryRecord]:
    """Load a trajectory record from disk.

    Args:
        issue_number: GitHub issue number.
        archive_dir: Archive directory.

    Returns:
        TrajectoryRecord or None if file does not exist.
    """
    filepath = archive_dir / trajectory_filename(issue_number)
    if not filepath.exists():
        return None

    data = json.loads(filepath.read_text(encoding="utf-8"))
    return TrajectoryRecord(
        issue_number=data.get("issue_number", issue_number),
        metadata=data.get("metadata"),
        metrics=data.get("metrics", []),
        comments_count=data.get("comments_count", 0),
        archiving_date=data.get("archiving_date", ""),
        outcome_score=data.get("outcome_score", 0.0),
    )


def load_all_trajectories(archive_dir: Path) -> List[TrajectoryRecord]:
    """Load all archived trajectory records.

    Args:
        archive_dir: Archive directory.

    Returns:
        List of TrajectoryRecord objects (one per JSON file).
    """
    records = []
    for filepath in sorted(archive_dir.glob("issue-*.json")):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            records.append(TrajectoryRecord(
                issue_number=data.get("issue_number", 0),
                metadata=data.get("metadata"),
                metrics=data.get("metrics", []),
                comments_count=data.get("comments_count", 0),
                archiving_date=data.get("archiving_date", ""),
                outcome_score=data.get("outcome_score", 0.0),
            ))
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[archive] Warning: Failed to load {filepath}: {e}")
    return records


def prune_archive(archive_dir: Path, max_archived: int = 200) -> int:
    """Remove oldest trajectory files if archive exceeds max_archived.

    Files are sorted by filename (which encodes issue number); lowest-numbered
    files are removed first. In practice older issues tend to have lower numbers.

    Args:
        archive_dir: Archive directory.
        max_archived: Maximum number of files to retain.

    Returns:
        Number of files deleted.
    """
    files = sorted(archive_dir.glob("issue-*.json"))
    deleted = 0
    while len(files) > max_archived:
        files.pop(0).unlink()
        deleted += 1
    return deleted


def list_archived(archive_dir: Path) -> List[Dict[str, Any]]:
    """Summarize all archived trajectories for display.

    Args:
        archive_dir: Archive directory.

    Returns:
        List of dicts with issue_number, outcome_score, metrics_count, archiving_date.
    """
    summaries = []
    for filepath in sorted(archive_dir.glob("issue-*.json")):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            summaries.append({
                "issue_number": data.get("issue_number", "?"),
                "outcome_score": data.get("outcome_score", 0.0),
                "metrics_count": len(data.get("metrics", [])),
                "archiving_date": data.get("archiving_date", ""),
            })
        except json.JSONDecodeError:
            pass
    return summaries


# ── Main Orchestrator ────────────────────────────────────────────────


def archive_trajectories(repo: str, token: str, archive_dir: Path,
                         max_issues: int = 50,
                         max_age_hours: Optional[int] = None,
                         max_archived: int = 200) -> int:
    """End-to-end fetch → extract → save pipeline.

    Args:
        repo: Repository in format "owner/name".
        token: GitHub API token.
        archive_dir: Local directory for storing trajectories.
        max_issues: Maximum closed issues to process.
        max_age_hours: If set, only archive issues closed within this window.
                       None means no recency filter.
        max_archived: Maximum files to keep in archive (prune if exceeded).

    Returns:
        Number of new/archived trajectories.
    """
    print(f"[archive] Fetching up to {max_issues} closed issues from {repo}...")
    issues = fetch_closed_issues(repo, token, max_issues=max_issues)
    print(f"[archive] Retrieved {len(issues)} closed issues.")

    if not issues:
        print("[archive] No closed issues found. Nothing to archive.")
        return 0

    # Optional: filter by recency
    if max_age_hours is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        filtered = []
        for issue in issues:
            closed_at = issue.get("closed_at")
            if closed_at:
                try:
                    closed_ts = datetime.fromisoformat(closed_at.replace("Z", "+00:00")).timestamp()
                    if closed_ts >= cutoff:
                        filtered.append(issue)
                except (ValueError, TypeError):
                    filtered.append(issue)  # Keep if date parsing fails
            else:
                filtered.append(issue)
        issues = filtered

    existing_files = set(f.stem for f in archive_dir.glob("issue-*.json"))
    new_count = 0
    updated_count = 0

    for issue in issues:
        issue_num = issue.get("number", 0)
        stem = trajectory_filename(issue_num).replace(".json", "")

        # Skip if already archived (unless we force refresh)
        if stem in existing_files:
            continue

        print(f"[archive] Processing issue #{issue_num}: {issue.get('title', '(no title)')}")
        comments = fetch_issue_comments(repo, issue_num, token)
        trajectory = build_trajectory(issue, comments)
        save_trajectory(trajectory, archive_dir)
        new_count += 1

    # Prune if needed
    total_files = len(list(archive_dir.glob("issue-*.json")))
    if total_files > max_archived:
        deleted = prune_archive(archive_dir, max_archived)
        print(f"[archive] Pruned {deleted} old trajectory files.")

    print(f"[archive] Completed: {new_count} new trajectories archived.")
    return new_count


def main():
    """CLI entry point."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print("[archive] Error: GITHUB_TOKEN and GITHUB_REPOSITORY env vars are required.")
        sys.exit(1)

    # Load RHO config for archive settings
    rho_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "rho_config.yaml")
    archive_path = ".rho/trajectories"
    max_issues = 50
    max_archived = 200

    try:
        import yaml
        if os.path.isfile(rho_config_path):
            with open(rho_config_path) as f:
                cfg = yaml.safe_load(f) or {}
            archive_cfg = cfg.get("archive", {})
            archive_path = archive_cfg.get("path", archive_path)
            max_archived = archive_cfg.get("max_archived", max_archived)
            coreset_cfg = cfg.get("coreset", {})
            max_issues = coreset_cfg.get("max_issues_scanned", max_issues)
    except Exception as e:
        print(f"[archive] Warning: Could not load rho_config.yaml: {e}")

    archive_dir = Path(archive_path)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"

    if cmd == "fetch":
        archive_trajectories(repo, token, archive_dir, max_issues=max_issues, max_archived=max_archived)
    elif cmd == "list":
        summaries = list_archived(archive_dir)
        if not summaries:
            print(f"[archive] No archived trajectories in {archive_dir}/")
            return
        print(f"[archive] Archived trajectories ({len(summaries)} total):")
        for s in summaries:
            score_str = f"{s['outcome_score']:.1f}" if isinstance(s["outcome_score"], float) else "?"
            print(
                f"  #{s['issue_number']:>4d}  "
                f"score={score_str:>5s}  "
                f"metrics={s['metrics_count']}  "
                f"date={s['archiving_date']}"
            )
    elif cmd == "prune":
        deleted = prune_archive(archive_dir, max_archived)
        print(f"[archive] Pruned {deleted} files.")
    else:
        print(f"[archive] Unknown command: {cmd}")
        print("[archive] Valid commands: fetch, list, prune")
        sys.exit(1)


if __name__ == "__main__":
    main()
