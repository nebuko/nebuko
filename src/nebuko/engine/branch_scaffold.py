"""Engine-owned branch scaffold: the session workspace is PR-ready before the agent.

The ``git-pr-workflow`` skill used to teach every ``dev`` run the same git
mechanics — fetch ``main``, create (or reuse) a ``feature/issue-<n>-<slug>``
branch, rebase it on ``origin/main`` — and the prompt carried the skill's
summary. That is engine work, not model work (issue #1138): the branch is
mechanical, the slug comes from the trigger, and a conflict is a fact about the
checkout, not a decision for the model.

:class:`BranchScaffold` closes this, engine-side, mirroring
:class:`~nebuko.engine.git_session.GitSessionGuard`:

* :meth:`BranchScaffold.scaffold` runs before the agent phase. It fetches
  ``origin/main`` only (a full fetch used to drag every stale branch into the
  checkout), creates the feature branch when it does not exist, and rebases an
  existing one on top of ``origin/main``.
* A rebase conflict is *not* fatal: the run proceeds and the conflict
  instruction is appended to the prompt, so fixing it is the agent's first
  action. Everything else (no remote, detached HEAD, a failed fetch) is a
  benign skip or a fail-closed error, reported as data — the module never
  raises.

The scaffolded branch is returned to the caller, which persists it as
``workflow_state.scaffold_branch`` so later runs reuse the same branch.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    # The lint-config job installs base deps (no openhands) yet must still
    # import this module through the engine package (issue #1129).
    get_logger = logging.getLogger

logger = get_logger(__name__)

# @stub-protected
DEFAULT_BASE_BRANCH: str = "main"

_GIT_TIMEOUT_S = 30
_FETCH_TIMEOUT_S = 120

#: Characters a branch slug drops; everything else collapses to ``-``.
_SLUG_STRIP = re.compile(r"[^a-zA-Z0-9._/-]+")
_SLUG_DASHES = re.compile(r"-{2,}")
#: Maximum slug length; branch names must stay comfortably under git's 255.
_SLUG_MAX = 60


def _dedent(text: str) -> str:
    """Collapse a multi-line git error into one log-friendly line."""
    return " ".join(text.split())


@dataclass(frozen=True)
class ScaffoldResult:
    """Outcome of one branch scaffold pass.

    ``ok`` is the pass/fail verdict; ``skipped`` marks the benign case where
    there was nothing to scaffold (``ok`` is then True and ``detail`` explains
    why); ``branch`` is the scaffolded feature branch (``None`` when skipped or
    failed); ``conflict`` carries the rebase-conflict instruction the caller
    must prepend to the prompt — an empty string when the rebase was clean.
    """

    ok: bool
    skipped: bool = False
    branch: str | None = None
    conflict: str = ""
    detail: str = ""


def branch_slug(title: str) -> str:
    """A branch-safe slug for an issue title (``Fix: the Thing!`` → ``fix-the-thing``).

    Non-alphanumerics collapse to single dashes; the result is lowercased and
    truncated so ``feature/issue-<n>-<slug>`` stays well under git's limits.
    """
    slug = _SLUG_STRIP.sub("-", title.strip().lower())
    slug = _SLUG_DASHES.sub("-", slug).strip("-/")
    return slug[:_SLUG_MAX].rstrip("-/")


def feature_branch_name(issue_number: int, title: str) -> str:
    """The feature branch for an issue: ``feature/issue-<n>-<slug>``.

    A missing number or an empty slug degrades to the bare prefix — the branch
    is still created, just without the parts the trigger could not supply.
    """
    slug = branch_slug(title)
    if issue_number > 0 and slug:
        return f"feature/issue-{issue_number}-{slug}"
    if issue_number > 0:
        return f"feature/issue-{issue_number}"
    if slug:
        return f"feature/{slug}"
    return "feature"


def issue_title_from_payload(payload: Mapping[str, Any]) -> str:
    """The issue title from the raw event payload, or ``""`` when unavailable.

    ``Trigger`` carries the number but not the title (issue #1138), so the slug
    reads the payload the trigger was built from.
    """
    issue = payload.get("issue")
    if isinstance(issue, Mapping):
        title = issue.get("title")
        if isinstance(title, str):
            return title
    return ""


class BranchScaffold:
    """Prepare the session workspace's feature branch before the agent runs.

    Args:
        repo_root: The real checkout (the engine's ``repo_root``).
        env: The run's environment, read for the base-branch override.
        remote: The remote every git command targets.
        base: The base branch to fetch and rebase onto (``BASE_BRANCH`` env
            override, else ``main``).
    """

    # @stub-protected
    def __init__(
        self,
        repo_root: Path,
        env: Mapping[str, str],
        remote: str = "origin",
        base: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.env = env
        self.remote = remote
        self.base = base or env.get("BASE_BRANCH") or DEFAULT_BASE_BRANCH

    # ── Entry point ───────────────────────────────────────────────

    # @stub-protected
    def scaffold(self, issue_number: int, issue_title: str) -> ScaffoldResult:
        """Fetch the base, then create or rebase the issue's feature branch.

        Returns:
            ``ok=True`` with the scaffolded ``branch``; a benign skip (no
            remote, detached HEAD) with ``skipped=True``; ``ok=False`` with
            actionable detail when the fetch or the branch creation fails. A
            rebase conflict is *not* a failure: ``ok=True`` with ``conflict``
            set, so the run proceeds and the agent fixes the conflict first.
        """
        if self._remote_url() is None:
            return ScaffoldResult(
                ok=True, skipped=True, detail="no git remote"
            )
        if self._current_branch() in ("", "HEAD"):
            return ScaffoldResult(
                ok=True, skipped=True, detail="detached HEAD"
            )

        fetch_problem = self._fetch_base()
        if fetch_problem:
            return ScaffoldResult(ok=False, detail=fetch_problem)

        branch = feature_branch_name(issue_number, issue_title)
        if self._branch_exists(branch):
            return self._rebase_existing(branch)
        return self._create_branch(branch)

    # ── Branch operations ─────────────────────────────────────────

    def _create_branch(self, branch: str) -> ScaffoldResult:
        """Create ``branch`` from the fetched base and check it out."""
        try:
            self._run_git(["checkout", "-b", branch, f"{self.remote}/{self.base}"])
        except RuntimeError as exc:
            return ScaffoldResult(
                ok=False,
                detail=f"could not create {branch} from "
                f"{self.remote}/{self.base}: {_dedent(str(exc))}",
            )
        logger.info("[phase:agent] branch scaffold: created %s", branch)
        return ScaffoldResult(
            ok=True, branch=branch, detail=f"created {branch}"
        )

    def _rebase_existing(self, branch: str) -> ScaffoldResult:
        """Check out the existing branch and rebase it on the fetched base.

        A conflict leaves the rebase in progress on purpose: the caller appends
        ``conflict`` to the prompt, the agent resolves it as its first action,
        and the git session finalize still proves the session ends published.
        """
        try:
            self._run_git(["checkout", branch])
        except RuntimeError as exc:
            return ScaffoldResult(
                ok=False,
                detail=f"could not check out {branch}: {_dedent(str(exc))}",
            )
        try:
            self._run_git(["rebase", f"{self.remote}/{self.base}"])
        except RuntimeError as exc:
            detail = _dedent(str(exc))
            conflicted = self._conflicted_paths()
            logger.warning(
                "[phase:agent] branch scaffold: rebase of %s conflicts — "
                "the agent must resolve it first",
                branch,
            )
            return ScaffoldResult(
                ok=True,
                branch=branch,
                conflict=_conflict_instruction(
                    branch, self.base, conflicted, detail
                ),
                detail=f"rebase of {branch} conflicts: {detail}",
            )
        logger.info("[phase:agent] branch scaffold: rebased %s", branch)
        return ScaffoldResult(
            ok=True, branch=branch, detail=f"rebased {branch}"
        )

    # ── Plumbing ──────────────────────────────────────────────────

    def _fetch_base(self) -> str:
        """Fetch only the base branch ref. Returns a problem or ``""``."""
        try:
            self._run_git(
                ["fetch", "--no-tags", self.remote, self.base],
                timeout=_FETCH_TIMEOUT_S,
            )
        except RuntimeError as exc:
            return f"could not fetch {self.remote}/{self.base}: {_dedent(str(exc))}"
        return ""

    def _branch_exists(self, branch: str) -> bool:
        try:
            self._run_git(["rev-parse", "--verify", branch])
        except RuntimeError:
            return False
        return True

    def _conflicted_paths(self) -> tuple[str, ...]:
        """The paths the in-progress rebase left conflicted, best-effort."""
        try:
            out = self._run_git(["diff", "--name-only", "--diff-filter=U"])
        except RuntimeError:
            return ()
        return tuple(line for line in out.splitlines() if line.strip())

    def _run_git(self, args: list[str], timeout: int = _GIT_TIMEOUT_S) -> str:
        """Run a git command in ``repo_root``; return stdout, raise on failure."""
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Never block on a credentials prompt: fail fast instead (no TTY
            # in CI, so a prompt would hang until the timeout).
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _remote_url(self) -> str | None:
        try:
            url = self._run_git(["remote", "get-url", self.remote])
        except (RuntimeError, subprocess.SubprocessError):
            return None
        return url or None

    def _current_branch(self) -> str:
        try:
            return self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        except (RuntimeError, subprocess.SubprocessError):
            return "HEAD"


def _conflict_instruction(
    branch: str, base: str, conflicted: tuple[str, ...], detail: str
) -> str:
    """The prompt instruction for an unresolved rebase (issue #1138)."""
    lines = [
        f"⚠️ The feature branch `{branch}` could not be rebased on `{base}` "
        "automatically: a rebase is already in progress with conflicts.",
        "",
        "**Fix the rebase conflict as your FIRST action**, before any other work:",
        "",
        "1. Run `git status` to list the conflicted files.",
        "2. Resolve every conflict, then `git add` the resolved files.",
        "3. Run `git rebase --continue` (never `git rebase --abort` unless the "
        "work itself must be discarded).",
    ]
    if conflicted:
        listed = ", ".join(f"`{path}`" for path in conflicted[:10])
        lines.append(f"\nConflicted files: {listed}")
    lines.append(f"\nRebase error: {detail}")
    return "\n".join(lines)
