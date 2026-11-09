"""Read-only checkout of the session branch for evidence steps (issue #1327).

Script steps and exec-style ``inputs`` used to run on the workflow's own ref,
because they execute before the branch scaffold prepares the dev branch. A
depth-1 checkout of that ref cannot even resolve ``origin/main...HEAD``, so the
"evidence" could describe a different commit than the one under review.

This module is the read-only counterpart of
:class:`nebuko.engine.lib.git.branch_scaffold.BranchScaffold`: it fetches the
base and the session branch, deepens a shallow checkout enough for a
three-dot diff, and checks the pushed SHA out detached. It never creates a
branch, never merges the base and never pushes — evidence must test exactly
what was pushed.

Like the scaffold, it never raises: a failure is returned as data and the
caller turns it into a run error. There is deliberately no fallback to the
workflow ref when the branch is expected but unreachable: that is the silent
wrong-commit evidence this module exists to prevent.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

from .branch_scaffold import DEFAULT_BASE_BRANCH, git_subprocess_env
from .git_session import GitSessionGuard

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvidenceCheckout:
    """The commit an evidence command runs on.

    ``ok`` is the pass/fail verdict; ``branch`` is the checked-out branch (empty
    for the workflow ref, which is reported as ``branch: none``); ``head_sha``
    is the exact commit the command will see; ``detail`` explains a failure.
    """

    ok: bool
    branch: str = ""
    head_sha: str = ""
    detail: str = ""


def current_checkout(repo_root) -> EvidenceCheckout:
    """Report the checkout as it stands — the ``checkout: workflow`` mode.

    Nothing is fetched or moved: the command runs on the workflow's own ref,
    exactly as before issue #1327.
    """
    head = _git(repo_root, ["rev-parse", "HEAD"])
    if head is None:
        return EvidenceCheckout(ok=False, detail=f"{repo_root} is not a git checkout")
    branch = _git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    return EvidenceCheckout(
        ok=True,
        branch="" if branch == "HEAD" else branch,
        head_sha=head,
    )


def resolve_evidence_checkout(
    repo_root,
    env: Mapping[str, str] | None,
    *,
    mode: str = "session",
    branch: str = "",
) -> EvidenceCheckout:
    """The checkout an evidence command runs on, by mode (issue #1327).

    ``workflow`` keeps the checkout as it stands. ``session`` checks the pushed
    session branch out detached; with no session branch yet (a script before
    any agent scaffolded one) it runs on the workflow ref and reports no
    branch, rather than treating the absent branch as unreachable.
    """
    if mode != "session" or not branch:
        current = current_checkout(repo_root)
        if current.ok and mode == "session":
            # No branch yet: the workflow ref, reported as ``branch: none``.
            return EvidenceCheckout(ok=True, branch="", head_sha=current.head_sha)
        return current
    return EvidenceCheckoutResolver(repo_root, env, branch=branch).resolve()


class EvidenceCheckoutResolver:
    """Check the session branch out detached, read-only (issue #1327).

    Args:
        repo_root: The session checkout the command runs in.
        env: The run's environment, read for the base-ref override and the
            git token (the same handling as :class:`GitSessionGuard`).
        branch: The session branch recorded by ``_record_scaffold_branch``.
    """

    def __init__(self, repo_root, env: Mapping[str, str] | None = None, branch: str = "") -> None:
        self.repo_root = repo_root
        self.env = dict(env or {})
        self.branch = branch

    def resolve(self) -> EvidenceCheckout:
        """Fetch and check out the pushed SHA; never raises."""
        try:
            return self._resolve()
        except Exception as exc:  # noqa: BLE001 - a failure is data, not a crash
            logger.error("evidence checkout crashed: %s", exc)
            return EvidenceCheckout(ok=False, detail=f"evidence checkout crashed: {exc}")

    def _resolve(self) -> EvidenceCheckout:
        if not self.branch:
            return EvidenceCheckout(
                ok=False, detail="no session branch to check out"
            )
        if _git(self.repo_root, ["rev-parse", "--is-inside-work-tree"]) is None:
            return EvidenceCheckout(
                ok=False, detail=f"{self.repo_root} is not a git checkout"
            )
        # Prove auth before fetching: the workflow checks out with
        # ``persist-credentials: false``, so a private repo needs the engine's
        # token handling (the same one the session guard uses).
        guard = GitSessionGuard(self.repo_root, self.env)
        auth = guard.preflight()
        if not auth.ok:
            return EvidenceCheckout(
                ok=False,
                detail=f"cannot reach the remote for {self.branch}: {auth.detail}",
            )
        base = self._base_branch()
        problem = self._fetch_ref(base)
        if problem:
            return EvidenceCheckout(ok=False, detail=problem)
        problem = self._fetch_ref(self.branch)
        if problem:
            return EvidenceCheckout(
                ok=False,
                detail=f"session branch {self.branch} is unreachable: {problem}",
            )
        # ``origin/main...HEAD`` needs the merge base, which a depth-1 clone
        # does not have (git refuses with "unrelated histories").
        problem = self._unshallow()
        if problem:
            return EvidenceCheckout(ok=False, detail=problem)
        problem = self._detach(self.branch)
        if problem:
            return EvidenceCheckout(ok=False, detail=problem)
        head = _git(self.repo_root, ["rev-parse", "HEAD"]) or ""
        logger.info(
            "evidence checkout: %s at %s", self.branch, head[:12] or "<unknown>"
        )
        return EvidenceCheckout(ok=True, branch=self.branch, head_sha=head)

    def _base_branch(self) -> str:
        override = self.env.get("GITHUB_BASE_REF", "").strip()
        if override:
            return override
        head = _git(
            self.repo_root, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]
        )
        return head.removeprefix("origin/") if head else DEFAULT_BASE_BRANCH

    def _fetch_ref(self, name: str) -> str:
        """Fetch one branch into its remote-tracking ref; a problem, or ``""``."""
        return self._git(
            "fetch", "--no-tags", "origin",
            f"+refs/heads/{name}:refs/remotes/origin/{name}",
        )

    def _unshallow(self) -> str:
        if _git(self.repo_root, ["rev-parse", "--is-shallow-repository"]) != "true":
            return ""
        problem = self._git("fetch", "--no-tags", "--unshallow", "origin")
        return f"could not deepen the checkout: {problem}" if problem else ""

    def _detach(self, branch: str) -> str:
        problem = self._git("checkout", "--quiet", "--detach", f"refs/remotes/origin/{branch}")
        return f"could not check out {branch}: {problem}" if problem else ""

    def _git(self, *args: str) -> str:
        """Run git; return a problem description, or ``""`` on success."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            env=git_subprocess_env(self.env),
        )
        if result.returncode != 0:
            return (result.stderr or result.stdout).strip() or f"git {' '.join(args)} failed"
        return ""


def _git(repo_root, args: list[str]) -> str | None:
    """Run a read-only git query; ``None`` when it fails."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=git_subprocess_env(None),
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()
