"""Deterministic PR fallback — commit, push, and open/update a PR.

Guarantees the "checkpoint the work in a PR" requirement of graceful
interruption (issue #489) even when the wind-down LLM run is uncooperative:

  1. Commit uncommitted working-tree changes (if any).
  2. Push the current branch to origin.
  3. Open a PR if none exists for the branch (idempotent).

Built on the same ``subprocess`` git pattern as ``remote_workflow._run_git``
and the ``gh`` CLI helpers in ``github_client.py``. Every ``gh`` call rides
:class:`github_client.GitHubTransport` so PR creation shares the same write
pacing, failure classification and bounded retries as comment publishing — a
content-creation secondary limit can no longer silently drop the PR (issue #805).
"""

import os
import subprocess
from dataclasses import dataclass
from subprocess import TimeoutExpired
from typing import Optional

from github_client import GitHubTransport
from openhands.sdk import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 120

#: Working-tree paths the engine itself writes into the checkout. They are not
#: agent output, so they never make the tree "dirty" for publication purposes.
#: Kept as a literal (not imported) because ``handoff_store`` imports this
#: module — it mirrors ``handoff_store.HANDOFF_RELATIVE_PATH``.
ENGINE_OWNED_PATHS = frozenset({".nebuko/handoff.md"})


def _repo_root() -> Optional[str]:
    """Checkout the git/gh commands must run in, or ``None`` for the process cwd.

    The in-process engine host runs the whole assembly under a temporary cwd
    (issue #740), so a bare ``git remote`` fails with "not a git repository".
    ``GFA_REPO_ROOT`` — exported by the engine's agent seam — pins git/gh to the
    real checkout so the wind-down can still commit, push and open the PR
    (issue #766).
    """
    root = os.environ.get("GFA_REPO_ROOT", "").strip()
    return root or None


def _run_git(
    args: list[str], timeout: int = DEFAULT_TIMEOUT, cwd: Optional[str] = None
) -> str:
    """Run a git command via subprocess with a timeout. Returns stdout."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd if cwd is not None else _repo_root(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _run_gh(
    args: list[str],
    timeout: int = DEFAULT_TIMEOUT,
    cwd: Optional[str] = None,
    *,
    mutative: bool = False,
    transport: Optional[GitHubTransport] = None,
) -> str:
    """Run a gh CLI command through the rate-limit-aware transport.

    Routing every ``gh`` call through :class:`GitHubTransport` gives PR
    creation the same write pacing, failure classification and bounded retries
    as comment publishing, so a content-creation secondary limit can no longer
    silently drop the PR (issue #805). Raises ``RuntimeError`` when the call
    cannot be delivered after the transport's retries.
    """
    transport = transport or GitHubTransport()
    result = transport.call(
        args,
        mutative=mutative,
        cwd=cwd if cwd is not None else _repo_root(),
        timeout=timeout,
    )
    if not result.ok:
        raise RuntimeError(
            f"gh {' '.join(args)} failed ({result.status}): {result.detail}"
        )
    return result.payload.strip()


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
    """Stage and commit untracked/modified tracked files. Returns the hash."""
    # Scope staging to untracked + modified tracked files only; deletions,
    # type changes and renames are left for the next run to handle in
    # context (keeps the automatic checkpoint conservative).
    out = _run_git(["status", "--porcelain"]).strip()
    paths = []
    for line in out.splitlines():
        code = line[:2]
        if code == "??" or " M" in code or "M " in code:
            paths.append(line[3:])
    if paths:
        _run_git(["add", "--"] + paths)
    _run_git(
        [
            "-c",
            "user.name=nebuko",
            "-c",
            "user.email=nebuko@users.noreply.github.com",
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


def _find_pr_for_branch(
    branch: str, *, transport: Optional[GitHubTransport] = None
) -> Optional[str]:
    """Return the URL of an open PR for the branch, or None."""
    out = _run_gh(
        ["pr", "list", "--head", branch, "--json", "url", "--jq", ".[0].url"],
        transport=transport,
    )
    url = out.strip().strip('"')
    return url or None


@dataclass(frozen=True)
class PublicationState:
    """Immutable result of :func:`check_publication`.

    Fields:
        published: True when the branch has no unpublished work left.
        reasons: Why the work counts as unpublished — a subset of
            ``{"dirty_tree", "unpushed_commits", "missing_pr"}``.
        dirty_paths: The working-tree paths that made ``dirty_tree`` fire
            (engine-owned and gitignored paths are already excluded).
        skip_reason: Set when the check could not run at all
            (``"no git remote"`` / ``"detached HEAD"``); ``published`` is True
            in that case and nothing is changed.
    """

    published: bool
    reasons: tuple[str, ...] = ()  # @stub-protected
    dirty_paths: tuple[str, ...] = ()
    skip_reason: str = ""


def _dirty_paths() -> tuple[str, ...]:
    """Working-tree paths git reports, minus the engine-owned ones."""
    out = _run_git(["status", "--porcelain"])
    paths = []
    for line in out.splitlines():
        path = line[3:].strip()
        # Renames render as "old -> new"; the new path is what matters.
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"')
        if not path or path in ENGINE_OWNED_PATHS:
            continue
        paths.append(path)
    return tuple(paths)


def _branch_holds_unpublished_commits(branch: str, remote: str) -> bool:
    """Whether HEAD has commits the remote does not have.

    With an upstream this is exactly ``@{u}..HEAD``. A branch that was never
    pushed has no upstream, so the comparison falls back to the repository's
    base branch — otherwise every fresh branch would look "unpushed" even
    when it holds nothing.
    """
    try:
        ahead = _run_git(["rev-list", "--count", "@{u}..HEAD"])
        return int(ahead or 0) > 0
    except RuntimeError:
        pass
    base = _default_base()
    try:
        ahead = _run_git(["rev-list", "--count", f"{remote}/{base}..HEAD"])
    except RuntimeError:
        # No remote-tracking ref for the base — compare against the local one.
        try:
            ahead = _run_git(["rev-list", "--count", f"{base}..HEAD"])
        except RuntimeError:
            ahead = "0"
    return int(ahead or 0) > 0


def check_publication() -> PublicationState:
    # @stub-protected: signature defined by architect — do not modify
    """Detect work that was never committed, pushed or opened as a PR.

    The publication guard (see ``run_agent.publication_guard``) calls this
    after an approved write step: the engine must not let a step end with the
    work sitting only in the checkout.

    Unpublished means any of:
      * the working tree holds changes (gitignored paths are invisible to
        ``git status``; engine-owned paths are excluded explicitly),
      * the current branch has commits the remote does not have,
      * the branch holds such commits but no open PR exists for it. (A branch
        with nothing on it is not "missing a PR" — opening an empty WIP PR
        would be noise.)

    Never raises, mirroring :func:`ensure_pr`: any failure is logged and
    reported as a skip so the caller degrades gracefully.
    """
    try:
        remote = _default_remote()
        if remote is None:
            logger.info("check_publication: no git remote configured — skipping")
            return PublicationState(published=True, skip_reason="no git remote")

        branch = _current_branch()
        if branch in ("HEAD", ""):
            logger.info("check_publication: detached HEAD — skipping")
            return PublicationState(published=True, skip_reason="detached HEAD")

        dirty = _dirty_paths()
        reasons: list[str] = []
        if dirty:
            reasons.append("dirty_tree")

        has_branch_work = _branch_holds_unpublished_commits(branch, remote)
        if has_branch_work:
            reasons.append("unpushed_commits")
            try:
                pr_url = _find_pr_for_branch(branch)
            except (RuntimeError, TimeoutExpired) as exc:
                # A failing PR lookup must not become a publication failure —
                # ensure_pr would fail the same way; surface it as a skip.
                logger.warning(f"check_publication: PR lookup failed: {exc}")
                return PublicationState(
                    published=True, skip_reason="pr lookup failed"
                )
            if not pr_url:
                reasons.append("missing_pr")

        return PublicationState(
            published=not reasons,
            reasons=tuple(reasons),
            dirty_paths=dirty,
        )
    except Exception as exc:  # noqa: BLE001 - must never break the run
        logger.warning(f"check_publication failed: {exc}")
        return PublicationState(published=True, skip_reason=f"error: {exc}")


def _open_pr(
    repo: str,
    head: str,
    base: str,
    title: str,
    issue_number: Optional[int],
    *,
    transport: Optional[GitHubTransport] = None,
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
    url = _run_gh(args, mutative=True, transport=transport).strip()
    if not url:
        # A dropped content-creation write can exit 0 with an empty body; treat
        # it as a failure so the PR is never silently lost (issue #805).
        raise RuntimeError("gh pr create returned no PR URL (write may have been dropped)")
    return url


def _issue_number_from_env() -> Optional[int]:
    """TARGET_NUMBER is the triggering issue number, or 0 when absent."""
    raw = os.environ.get("TARGET_NUMBER", "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        return None
    return value if value > 0 else None


def _default_base(*, transport: Optional[GitHubTransport] = None) -> str:
    """Resolve the PR base branch for the current repository.

    GITHUB_BASE_REF is only set on pull_request triggers; on the
    issues/issue_comment/workflow_call triggers this repo uses it is empty,
    so the repository's default branch must be queried explicitly. ``gh``
    resolves the target repo from the ambient git context (remote origin /
    GITHUB_REPOSITORY), so no URL parsing is needed.
    """
    base = os.environ.get("GITHUB_BASE_REF", "").strip()
    if base:
        return base
    try:
        out = _run_gh(
            ["api", "repos/{owner}/{repo}", "--jq", ".default_branch"],
            transport=transport,
        ).strip().strip('"')
        if out:
            return out
    except (RuntimeError, TimeoutExpired):
        # A failing/timing-out network query must degrade to a logged
        # fallback ("main"), never escalate into the whole ensure_pr result.
        logger.warning("_default_base: gh api failed, using 'main'")
    return "main"


def ensure_pr(dry_run: bool = False) -> EnsurePrResult:
    """Commit/push/open a PR for the current branch — idempotent.

    Never raises: any failure is logged and reported in the returned
    :class:`EnsurePrResult` so the caller (build_output) degrades gracefully.

    Args:
        dry_run: If True, log intended actions without mutating git state.

    Returns:
        EnsurePrResult describing what happened.
    """
    # One transport for the whole flow so the PR lookup and the PR creation
    # share a single pacing/budget state (issue #805).
    transport = GitHubTransport()
    try:
        remote = _default_remote()
        if remote is None:
            logger.info("ensure_pr: no git remote configured — skipping")
            return EnsurePrResult(skipped=True, message="no git remote")

        branch = _current_branch()
        if branch in ("HEAD", ""):
            logger.info("ensure_pr: detached HEAD — skipping")
            return EnsurePrResult(skipped=True, message="detached HEAD")

        head_hash = _run_git(["rev-parse", "HEAD"])
        commit_hash = ""
        if _has_uncommitted_changes():
            if dry_run:
                logger.info("[DRY RUN] ensure_pr: would commit uncommitted changes")
            else:
                commit_hash = _commit_pending_changes()
                logger.info(
                    f"ensure_pr: committed pending changes at {commit_hash[:12]}"
                )
        # Dry run performs no commit, so report the current HEAD.
        commit_hash = commit_hash or head_hash

        pushed = False
        if not dry_run:
            _push_branch(branch, remote)
            pushed = True
            logger.info(f"ensure_pr: pushed branch '{branch}' to {remote}")

        existing = (
            _find_pr_for_branch(branch, transport=transport) if not dry_run else None
        )
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
        base = _default_base(transport=transport)
        issue_number = _issue_number_from_env()
        title = (
            "WIP checkpoint: in-flight work from interrupted agent run "
            f"(#{issue_number or 'n/a'})"
        )
        pr_url = _open_pr(
            repo, branch, base, title, issue_number, transport=transport
        )
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
