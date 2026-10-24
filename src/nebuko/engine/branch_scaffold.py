"""Engine-owned branch scaffold: prepare the dev session's feature branch.

The ``git-pr-workflow`` skill used to spend agent turns on branch mechanics
(issue #1138): fetch the base branch, create or rebase the feature branch,
resolve rebase conflicts first. The engine now does all of it before the
prompt is built, so the agent starts on a ready branch and the skill (and its
prompt section) can go.

:class:`BranchScaffold.scaffold` runs in the ``prompt`` phase, before
:meth:`Engine.build_prompt`:

* fetches the base branch only (``--no-tags``, single refspec);
* creates ``feature/issue-<n>-<slug>`` from ``origin/<base>`` when it does not
  exist, or rebases the existing branch onto ``origin/<base>``;
* a rebase conflict is returned as data (fix-first instruction plus the
  conflicted paths) instead of failing the run — the note is appended to the
  prompt so the agent fixes the conflict before anything else;
* no remote or an unborn/detached HEAD is a benign skip (nothing to scaffold);
* a failed fetch or branch creation is fail-closed — the caller turns it into
  a run error, mirroring :class:`nebuko.engine.git_session.GitSessionGuard`.

Both entry points never raise: a failure is returned as data, like
:class:`GitSessionGuard`.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    # The lint-config job installs base deps (no openhands) yet must still
    # import this module through the engine package (issue #1129).
    get_logger = logging.getLogger

logger = get_logger(__name__)

#: Env var overriding the base branch (CI sets it on pull_request triggers).
BASE_BRANCH_ENV: str = "GITHUB_BASE_REF"

#: Default base branch when the remote exposes no ``main``.
DEFAULT_BASE_BRANCH: str = "main"

_GIT_TIMEOUT_S = 60
_FETCH_TIMEOUT_S = 120

_SLUG_MAX_LEN = 50
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_DASH_RE = re.compile(r"-+")


def branch_slug(title: str) -> str:
    """Lowercase-hyphen slug of an issue title, safe for a git branch name.

    Non-alphanumerics collapse to single hyphens, leading/trailing hyphens are
    dropped and the slug is capped so the branch name stays readable.
    """
    slug = _SLUG_STRIP_RE.sub("-", title.strip().lower())
    slug = _SLUG_DASH_RE.sub("-", slug).strip("-")
    return slug[:_SLUG_MAX_LEN].rstrip("-")


def feature_branch_name(issue_number: int, title: str) -> str:
    """The dev session's branch: ``feature/issue-<n>-<slug>``."""
    return f"feature/issue-{issue_number}-{branch_slug(title)}"


def issue_title_from_payload(payload: Mapping[str, Any]) -> str:
    """The issue title out of the raw event payload (``""`` when absent).

    Handles both an ``issues`` entry (opened/edited events) and the flat
    ``issue`` object of comment events.
    """
    issue = payload.get("issue")
    if isinstance(issue, Mapping):
        return str(issue.get("title") or "")
    return ""


@dataclass(frozen=True)
class ScaffoldResult:
    """Outcome of one branch scaffold pass.

    ``ok`` is the pass/fail verdict; ``skipped`` marks the benign case where
    there was nothing to scaffold (``ok`` is then True and ``detail`` explains
    why); ``conflict`` marks the rebase-conflict case (``ok`` is True — the
    agent session starts, but must fix the conflict first); ``detail`` is
    actionable prose; ``branch`` is the prepared branch name; ``note`` is the
    fix-first instruction appended to the prompt on a conflict.
    """

    ok: bool
    skipped: bool = False
    conflict: bool = False
    detail: str = ""
    branch: str = ""
    note: str = ""


class BranchScaffold:
    """Prepare the feature branch a dev session works on.

    Args:
        repo_root: The real checkout (``GFA_REPO_ROOT`` / the engine's
            ``repo_root``) — the agent host may run under a temporary cwd.
        env: The run's environment, read for the base-branch override.
        remote: The remote every git command targets.
        issue_number: The triggering issue number (``0`` when unavailable).
        issue_title: The triggering issue title (``""`` when unavailable).
    """

    # @stub-protected
    def __init__(
        self,
        repo_root: Path,
        env: Mapping[str, str],
        issue_number: int = 0,
        issue_title: str = "",
        remote: str = "origin",
    ) -> None:
        self.repo_root = Path(repo_root)
        self.env = env
        self.remote = remote
        self.issue_number = issue_number
        self.issue_title = issue_title

    # ── Entry points ──────────────────────────────────────────────

    # @stub-protected
    def scaffold(self) -> ScaffoldResult:
        """Fetch the base, then create or rebase the feature branch.

        Returns:
            ``ok=True`` with the prepared ``branch`` on success, a benign skip
            when there is nothing to scaffold (no remote, unborn or detached
            HEAD, no issue number), ``ok=True`` with ``conflict=True`` and a
            fix-first ``note`` when the rebase left conflicts, and
            ``ok=False`` with actionable detail when the scaffold itself
            failed (fetch, branch creation).
        """
        if not self._is_git_checkout():
            return ScaffoldResult(
                ok=False, detail=f"{self.repo_root} is not a git checkout"
            )
        if self._remote_url() is None:
            return ScaffoldResult(ok=True, skipped=True, detail="no git remote")
        if self._current_branch() in ("", "HEAD"):
            return ScaffoldResult(
                ok=True, skipped=True, detail="detached HEAD"
            )
        if self.issue_number <= 0:
            return ScaffoldResult(
                ok=True,
                skipped=True,
                detail="no issue number: the branch is left untouched",
            )

        base = self._base_branch()
        fetch_failure = self._fetch_base(base)
        if fetch_failure is not None:
            return ScaffoldResult(
                ok=False, detail=f"could not fetch {self.remote}/{base}: {fetch_failure}"
            )

        branch = feature_branch_name(self.issue_number, self.issue_title)
        if self._branch_exists(branch):
            return self._rebase_existing(branch, base)
        return self._create_from_base(branch, base)

    # ── Branch operations ─────────────────────────────────────────

    # @stub-protected
    def _create_from_base(self, branch: str, base: str) -> ScaffoldResult:
        """Create ``branch`` at ``origin/<base>`` and check it out."""
        try:
            self._run_git(["checkout", "-b", branch, f"{self.remote}/{base}"])
        except RuntimeError as exc:
            return ScaffoldResult(
                ok=False,
                detail=f"could not create {branch} from {self.remote}/{base}: "
                f"{_dedent(str(exc))}",
            )
        logger.info("branch scaffold: created %s from %s/%s", branch, self.remote, base)
        return ScaffoldResult(
            ok=True, branch=branch, detail=f"created {branch} from {self.remote}/{base}"
        )

    # @stub-protected
    def _rebase_existing(self, branch: str, base: str) -> ScaffoldResult:
        """Check out ``branch`` and rebase it onto ``origin/<base>``.

        A conflict is data, not a failure: the agent session still starts, but
        the returned ``note`` instructs it to resolve the conflict before any
        other work (the engine appends it to the prompt).
        """
        try:
            self._run_git(["checkout", branch])
        except RuntimeError as exc:
            return ScaffoldResult(
                ok=False,
                detail=f"could not check out {branch}: {_dedent(str(exc))}",
            )
        try:
            self._run_git(["rebase", f"{self.remote}/{base}"])
        except RuntimeError as exc:
            conflicted = self._conflicted_paths()
            self._abort_rebase()
            note = (
                f"Your branch {branch} has rebase conflicts with "
                f"{self.remote}/{base}. Resolve them as your FIRST action "
                f"(git status, edit the files, git add them, then `git "
                f"rebase --continue`) before any other work."
            )
            detail = f"rebase of {branch} onto {self.remote}/{base} has conflicts"
            if conflicted:
                detail += f": {', '.join(conflicted)}"
            else:
                detail += f": {_dedent(str(exc))}"
            logger.warning("branch scaffold: %s", detail)
            return ScaffoldResult(
                ok=True, conflict=True, detail=detail, branch=branch, note=note
            )
        logger.info("branch scaffold: rebased %s onto %s/%s", branch, self.remote, base)
        return ScaffoldResult(
            ok=True,
            branch=branch,
            detail=f"rebased {branch} onto {self.remote}/{base}",
        )

    # @stub-protected
    def _abort_rebase(self) -> None:
        """Restore the pre-rebase branch state; a failed abort is logged only.

        The conflict note stays accurate either way — the agent resolves the
        conflict from ``git status`` — but a half-applied rebase must not
        silently leave the session mid-rebase when the abort succeeds.
        """
        try:
            self._run_git(["rebase", "--abort"])
        except RuntimeError as exc:
            logger.warning(
                "branch scaffold: could not abort the conflicted rebase: %s", exc
            )

    # ── Plumbing ─────────────────────────────────────────────────

    # @stub-protected
    def _base_branch(self) -> str:
        """The base branch: the env override, else the remote's HEAD, else ``main``."""
        override = (self.env.get(BASE_BRANCH_ENV) or "").strip()
        if override:
            return override
        try:
            head = self._run_git(
                ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"]
            )
        except RuntimeError:
            return DEFAULT_BASE_BRANCH
        # ``origin/main`` → ``main``
        return head.partition("/")[2] or DEFAULT_BASE_BRANCH

    # @stub-protected
    def _fetch_base(self, base: str) -> Optional[str]:
        """Fetch only the base branch ref, no tags. Returns the failure or ``None``."""
        refspec = f"+refs/heads/{base}:refs/remotes/{self.remote}/{base}"
        try:
            self._run_git(
                ["fetch", "--no-tags", self.remote, refspec],
                timeout=_FETCH_TIMEOUT_S,
            )
        except RuntimeError as exc:
            return _dedent(str(exc))
        return None

    # @stub-protected
    def _conflicted_paths(self) -> tuple[str, ...]:
        """Paths the conflicted rebase left unmerged, in git's order."""
        try:
            out = self._run_git(["diff", "--name-only", "--diff-filter=U"])
        except RuntimeError:
            return ()
        return tuple(line for line in out.splitlines() if line.strip())

    # @stub-protected
    def _run_git(self, args: list[str], timeout: int = _GIT_TIMEOUT_S) -> str:
        """Run a git command in ``repo_root``; return stdout, raise on failure."""
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.repo_root),
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

    def _is_git_checkout(self) -> bool:
        try:
            self._run_git(["rev-parse", "--is-inside-work-tree"])
        except (RuntimeError, OSError):
            return False
        return True

    def _remote_url(self) -> Optional[str]:
        """The configured URL of ``self.remote``, or ``None`` when it is absent."""
        try:
            out = self._run_git(["remote", "get-url", self.remote]).strip()
        except (RuntimeError, OSError):
            return None
        return out or None

    def _current_branch(self) -> str:
        try:
            return self._run_git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        except (RuntimeError, OSError):
            return ""

    def _branch_exists(self, branch: str) -> bool:
        try:
            self._run_git(["rev-parse", "--verify", "--quiet", branch])
        except RuntimeError:
            return False
        return True


def _dedent(text: str) -> str:
    """Collapse a multi-line git error into one log-friendly line."""
    return " ".join(text.split())
