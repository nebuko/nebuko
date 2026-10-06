"""
Remote workflow fetching and caching via shallow git clones.

Provides functions to clone/fetch a specific commit or tag from a remote
git repository, locate a workflow YAML file within it, and manage a
local cache of previously fetched repos.

Usage:
    from remote_workflow import fetch_remote_workflow

    path = fetch_remote_workflow(
        repo="https://github.com/org/shared-workflows.git",
        ref="v1.2.3",
        workflow_name="deploy.yaml",
    )
"""

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

# Default cache directory: ~/.gh-workflows/cache/
DEFAULT_CACHE_DIR = Path.home() / ".gh-workflows" / "cache"
DEFAULT_TIMEOUT = 120


def cache_dir() -> Path:
    """Return the cache directory, creating it if it doesn't exist."""
    dir_path = Path(os.environ.get("WORKFLOW_CACHE_DIR", str(DEFAULT_CACHE_DIR)))
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def cache_key(repo: str, ref: str) -> str:
    """Generate a short SHA-256 hash for use as a cache directory name."""
    raw = f"{repo}:{ref}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_cached_repo_path(repo: str, ref: str) -> Optional[str]:
    """Return the filesystem path of a cached repo if it exists, else None."""
    repo_dir = cache_dir() / cache_key(repo, ref)
    if repo_dir.is_dir():
        return str(repo_dir)
    return None


def _run_git(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run a git command via subprocess with a timeout. Returns stdout."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}):\n"
            f"stderr: {result.stderr}"
        )
    return result.stdout


def _find_workflow_file(repo_path: str, workflow_name: Optional[str]) -> str:
    """Locate the workflow YAML file within a cloned repo.

    Search order:
    1. If workflow_name is provided: check repo root, then workflows/ subdir
    2. Otherwise: check workflows/default.yaml, then repo root default.yaml
    """
    candidates: list[str] = []

    if workflow_name:
        candidates.append(str(Path(repo_path) / workflow_name))
        candidates.append(str(Path(repo_path) / "workflows" / workflow_name))
    else:
        candidates.append(str(Path(repo_path) / "workflows" / "default.yaml"))
        candidates.append(str(Path(repo_path) / "default.yaml"))

    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate

    available = list(Path(repo_path).rglob("*.yaml"))
    available_str = "\n".join(f"  - {p}" for p in available[:10])
    label = workflow_name or "default.yaml"
    raise FileNotFoundError(
        f"Workflow file '{label}' not found in repo at {repo_path}.\n"
        f"Available YAML files:\n{available_str}"
    )


def fetch_remote_workflow(
    repo: str,
    ref: str,
    workflow_name: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Fetch a workflow YAML from a remote git repository.

    Uses a shallow clone with caching: on subsequent calls for the same
    (repo, ref) pair, performs a cheap ``git fetch`` instead of a full clone.

    Args:
        repo: HTTPS git repository URL.
        ref: Git commit SHA, tag, or branch name.
        workflow_name: Workflow filename within the repo (e.g., "deploy.yaml").
            If omitted, looks for workflows/default.yaml or default.yaml.
        timeout: Timeout in seconds for git operations.

    Returns:
        Absolute filesystem path to the workflow YAML file.
    """
    key = cache_key(repo, ref)
    repo_dir = cache_dir() / key

    if repo_dir.exists():
        # Update existing cache with a shallow fetch
        try:
            _run_git(["-C", str(repo_dir), "fetch", "--depth", "1", "origin", ref], timeout)
            _run_git(["-C", str(repo_dir), "checkout", ref], timeout)
        except RuntimeError as e:
            # Fetch failed — remove stale cache and fall through to full clone
            shutil.rmtree(repo_dir, ignore_errors=True)
            print(f"Warning: Stale cache removed for {repo}@{ref}: {e}", flush=True)
    else:
        # Fresh shallow clone
        _run_git(
            ["clone", "--depth", "1", "--branch", ref, repo, str(repo_dir)],
            timeout,
        )

    return _find_workflow_file(str(repo_dir), workflow_name)


def cleanup_cache(ttl_seconds: int = 3600) -> list[str]:
    """Remove expired entries from the workflow cache.

    Args:
        ttl_seconds: Time-to-live in seconds. Entries older than this are removed.

    Returns:
        List of removed directory names.
    """
    now = time.time()
    removed: list[str] = []
    base = cache_dir()

    if not base.is_dir():
        return removed

    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        mtime = entry.stat().st_mtime
        if now - mtime > ttl_seconds:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)

    return removed


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Manage workflow cache")
    sub = parser.add_subparsers(dest="command")
    clean_parser = sub.add_parser("clean")
    clean_parser.add_argument("--ttl", type=int, default=3600, help="TTL in seconds")
    args = parser.parse_args()

    if args.command == "clean":
        removed = cleanup_cache(args.ttl)
        if removed:
            print(f"Removed {len(removed)} cached repos: {', '.join(removed)}")
        else:
            print("Cache is clean.")
    else:
        parser.print_help()
