"""Deterministic PR fallback — commit, push, and open/update a PR.

Guarantees the "checkpoint the work in a PR" requirement of graceful
interruption (issue #489) even when the wind-down LLM run is uncooperative:

  1. Commit uncommitted working-tree changes (if any).
  2. Push the current branch to origin.
  3. Open a PR if none exists for the branch (idempotent).

Built on the same ``subprocess`` git pattern as ``remote_workflow._run_git``
and the ``gh`` CLI helpers in ``github_client.py``.
"""

import os
import subprocess
from dataclasses import dataclass
from typing import Optional

from openhands.sdk import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 120


def _run_git(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run a git command via subprocess with a timeout. Returns stdout."""
    result = subprocess.run(
        ["git"] + args, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _run_gh(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run a gh CLI command via subprocess. Returns stdout."""
    result = subprocess.run(
        ["gh"] + args, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


@dataclass(frozen=True)
class EnsurePrResult:
    """Immutable outcome of the deterministic PR fallback."""

    skipped: bool = False
    error: bool = False
    commit_hash: str = ""
    pushed: bool = False
    pr_url: str = ""
    message: str = ""


def _current_branch() -> str:
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"])


def _default_remote() -> Optional[str]:
    """Return the default push remote name, or None if remotes are missing."""
    out = _run_git(["remote"]).strip()
    return out.splitlines()[0].strip() if out else None


def _has_uncommitted_changes() -> bool:
    return bool(_run_git(["status", "--porcelain"]).strip())


def _commit_pending_changes() -> str:
    """Stage and commit all working-tree changes. Returns the new commit hash."""
    _run_git(["add", "-A"])
    _run_git(
        [
            "-c",
            "user.name=git-flow-ai",
            "-c",
            "user.email=git-flow-ai@users.noreply.github.com",
            "commit",
            "-m",
            "WIP checkpoint — automatic commit from graceful agent interruption\n\n"
            "The agent hit its soft time budget. This checkpoint preserves the "
            "in-flight work so the next run can resume instead of starting over.\n\n"
            "(auto-committed by the ensure_pr fallback, issue #489)",
        ]
    )
    return _run_git(["rev-parse", "HEAD"])


def _push_branch(branch: str, remote: str) -> None:
    args = ["push", remote, branch]
    try:
        upstream = _run_git(["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"])
    except RuntimeError:
        upstream = ""
    if not upstream:
        args += ["--set-upstream"]
    _run_git(args)


def _find_pr_for_branch(branch: str) -> Optional[str]:
    """Return the URL of an open PR for the branch, or None."""
    out = _run_gh(
        ["pr", "list", "--head", branch, "--json", "url", "--jq", ".[0].url"]
    )
    url = out.strip().strip('"')
    return url or None


def _open_pr(
    repo: str,
    head: str,
    base: str,
    title: str,
    issue_number: Optional[int],
) -> str:
    body_lines = [
        "_This PR was opened automatically by the ensure_pr fallback because the "
        "agent reached its soft time budget (issue #489). It contains the "
        "checkpointed work from that run; a follow-up run will pick up from here._",
    ]
    if issue_number:
        body_lines.append("")
        body_lines.append(f"Closes #{issue_number}")
    args = [
        "pr",
        "create",
        "--repo",
        repo,
        "--head",
        head,
        "--base",
        base,
        "--title",
        title,
        "--body",
        "\n".join(body_lines),
    ]
    # The issue is linked via the "Closes #N" reference in the body above
    # (gh pr create has no dedicated --issue flag).
    return _run_gh(args).strip()


def _parse_int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def ensure_pr(dry_run: bool = False) -> EnsurePrResult:
    """Commit/push/open a PR for the current branch — idempotent.

    Never raises: any failure is logged and reported in the returned
    :class:`EnsurePrResult` so the caller (build_output) degrades gracefully.

    Args:
        dry_run: If True, log intended actions without mutating git state.

    Returns:
        EnsurePrResult describing what happened.
    """
    try:
        remote = _default_remote()
        if remote is None:
            logger.info("ensure_pr: no git remote configured — skipping")
            return EnsurePrResult(skipped=True, message="no git remote")

        branch = _current_branch()
        if branch in ("HEAD", ""):
            logger.info("ensure_pr: detached HEAD — skipping")
            return EnsurePrResult(skipped=True, message="detached HEAD")

        commit_hash = ""
        if _has_uncommitted_changes():
            if dry_run:
                logger.info("[DRY RUN] ensure_pr: would commit uncommitted changes")
            else:
                commit_hash = _commit_pending_changes()
                logger.info(
                    f"ensure_pr: committed pending changes at {commit_hash[:12]}"
                )
        else:
            commit_hash = _run_git(["rev-parse", "HEAD"])

        pushed = False
        if not dry_run:
            _push_branch(branch, remote)
            pushed = True
            logger.info(f"ensure_pr: pushed branch '{branch}' to {remote}")

        existing = _find_pr_for_branch(branch) if not dry_run else None
        if existing:
            logger.info(
                f"ensure_pr: PR already exists for '{branch}': {existing}"
            )
            return EnsurePrResult(
                commit_hash=commit_hash,
                pushed=pushed,
                pr_url=existing,
                message=f"PR already exists: {existing}",
            )

        if dry_run:
            logger.info(f"[DRY RUN] ensure_pr: would open a PR for '{branch}'")
            return EnsurePrResult(
                commit_hash=commit_hash, message="[dry run] would open PR"
            )

        repo = _run_git(["remote", "get-url", remote])
        base = os.environ.get("GITHUB_BASE_REF") or "main"
        issue_number = _parse_int_env("TARGET_NUMBER")
        title = (
            "WIP checkpoint: in-flight work from interrupted agent run "
            f"(#{issue_number or 'n/a'})"
        )
        pr_url = _open_pr(repo, branch, base, title, issue_number)
        logger.info(f"ensure_pr: opened PR {pr_url}")
        return EnsurePrResult(
            commit_hash=commit_hash,
            pushed=pushed,
            pr_url=pr_url,
            message=f"PR opened: {pr_url}",
        )
    except Exception as e:
        logger.error(f"ensure_pr failed: {e}")
        return EnsurePrResult(error=True, message=f"error: {e}")