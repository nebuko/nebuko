"""Engine-owned branch scaffold: prepare the dev session's feature branch.

The ``git-pr-workflow`` skill used to spend agent turns on branch mechanics
(issue #1138). The engine now prepares the branch before the prompt is built:
it fetches the base branch only, creates ``feature/issue-<n>-<slug>`` from it
(or merges the existing one onto the base), and hands a merge conflict back as
data so the prompt can carry a fix-first note instead of the run dying.

Like :class:`nebuko.engine.lib.git.git_session.GitSessionGuard`, the scaffold never
raises: a failure is returned as data and the caller turns it into a run
error — an unscaffolded session must not look green.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    # The lint-config job installs base deps (no openhands) yet must still
    # import this module through the engine package (issue #1129).
    get_logger = logging.getLogger

logger = get_logger(__name__)

#: The default base branch when neither the env nor the remote HEAD names one.
DEFAULT_BASE_BRANCH = "main"


@dataclass(frozen=True)
class ScaffoldResult:
    """Outcome of one branch scaffold pass.

    ``ok`` is the pass/fail verdict; ``skipped`` marks the benign case where
    there was nothing to do (``ok`` is then True and ``detail`` explains why);
    ``conflict`` marks a merge that hit conflicts (the run continues, the
    prompt carries ``note``); ``branch`` is the prepared branch, empty when
    none was prepared.
    """

    ok: bool
    skipped: bool = False
    conflict: bool = False
    detail: str = ""
    branch: str = ""
    note: str = ""


def branch_slug(title: str) -> str:
    """The branch slug out of an issue title: lowercase, hyphenated, capped."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:50].rstrip("-")


def feature_branch_name(issue_number: int, title: str) -> str:
    """The dev session's branch: ``feature/issue-<n>-<slug>``."""
    return f"feature/issue-{issue_number}-{branch_slug(title)}"


def issue_title_from_payload(payload: object) -> str:
    """The issue title out of a trigger payload, or ``""``."""
    if isinstance(payload, dict):
        issue = payload.get("issue")
        if isinstance(issue, dict):
            title = issue.get("title")
            if isinstance(title, str):
                return title
    return ""


# @stub-protected
def _git_subprocess_env(env: Mapping[str, str] | None) -> dict[str, str]:
    """Bounded env for git subprocesses: ambient env plus git/credential keys.

    The session env carries unbounded JSON payloads (CONTEXT_BODIES,
    STEP_INPUT_RESULTS) that count toward the kernel's E2BIG argv+env limit
    (issue: "Argument list too long" crash). Only GIT_* and *TOKEN*/*CREDENTIAL*
    keys from ``env`` are overlaid onto ``os.environ``. ``env`` itself remains
    the Python-side source for base-ref overrides (``GITHUB_BASE_REF``).
    """
    raise NotImplementedError


class BranchScaffold:
    """Prepare the session's feature branch from the base branch."""

    def __init__(
        self,
        repo_root,
        env: dict[str, str] | None = None,
        *,
        issue_number: int = 0,
        issue_title: str = "",
    ) -> None:
        self.repo_root = repo_root
        self.env = dict(env or {})
        self.issue_number = issue_number
        self.issue_title = issue_title

    # @stub-protected
    def scaffold(self) -> ScaffoldResult:
        """Create or merge the feature branch onto the base; never raises.

        Returns:
            ``ok=True`` with ``branch`` when the branch is ready, a benign
            skip when there was nothing to do, and ``ok=False`` with
            actionable detail when the branch could not be prepared.
        """
        try:
            return self._scaffold()
        except Exception as exc:  # noqa: BLE001 - never crash the host
            logger.error("branch scaffold crashed: %s", exc)
            return ScaffoldResult(ok=False, detail=f"branch scaffold crashed: {exc}")

    # ── Steps ────────────────────────────────────────────────────

    def _scaffold(self) -> ScaffoldResult:
        if self.issue_number <= 0:
            return ScaffoldResult(
                ok=True, skipped=True, detail="no issue number to scaffold for"
            )
        if not (self.repo_root / ".git").exists():
            return ScaffoldResult(
                ok=False, detail=f"{self.repo_root} is not a git checkout"
            )
        if not self._git("remote").split():
            return ScaffoldResult(ok=True, skipped=True, detail="no git remote")
        try:
            current = self._git("rev-parse", "--abbrev-ref", "HEAD")
        except RuntimeError:
            # An unborn HEAD (no commits yet) reports "HEAD" with a fatal on
            # stderr: nothing to branch from, the session has no work either.
            current = "HEAD"
        if current == "HEAD":
            # A pinned-SHA checkout (this repo's own trigger workflow) is
            # detached: the scaffold must still prepare the feature branch,
            # or nobody would (issue #1138 round 6). Only an unborn HEAD
            # (no commits yet) has nothing to branch from.
            try:
                self._git("rev-parse", "--verify", "-q", "HEAD")
            except RuntimeError:
                return ScaffoldResult(ok=True, skipped=True, detail="unborn HEAD")
        branch = feature_branch_name(self.issue_number, self.issue_title)
        base = self._base_branch()
        # A pinned-SHA checkout has no remote-tracking refs at all: the base
        # must be fetched before anything can be created from it (round 8).
        if not self._fetch_base(base):
            return ScaffoldResult(
                ok=False, detail=f"could not fetch origin/{base}"
            )
        return self._prepare_branch(branch, base)

    def _base_branch(self) -> str:
        """The base branch: ``GITHUB_BASE_REF`` when set, else the remote HEAD."""
        override = self.env.get("GITHUB_BASE_REF", "").strip()
        if override:
            return override
        try:
            head = self._git(
                "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
            )
            return head.removeprefix("origin/")
        except RuntimeError:
            return DEFAULT_BASE_BRANCH

    def _fetch_base(self, base: str) -> bool:
        """Fetch the base branch only (issue #1138: no tags, no full history)."""
        try:
            self._git("fetch", "--no-tags", "origin", base)
            return True
        except RuntimeError as exc:
            logger.warning("branch scaffold: fetch failed: %s", exc)
            return False

    def _prepare_branch(self, branch: str, base: str) -> ScaffoldResult:
        """Continue an existing branch, or create it from the fetched base.

        An existing branch — local, or remote-only from a previous round —
        must be merged onto the base, never reset: ``checkout -B`` would
        orphan the prior round's commits (issue #1138 round 8).
        """
        if self._branch_exists(branch):
            return self._merge_origin(branch, base)
        return self._create_branch(branch, base)

    def _branch_exists(self, branch: str) -> bool:
        """Whether the branch exists locally or on the remote.

        A fresh pinned-SHA checkout has no local branches: a branch that only
        exists on the remote (a previous round's work) is fetched so the
        session continues it.
        """
        try:
            if self._git("rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
                return True
        except RuntimeError:
            pass  # no local branch: look on the remote
        try:
            if not self._git(
                "ls-remote", "--heads", "origin", f"refs/heads/{branch}"
            ):
                return False
            # Populates ``refs/remotes/origin/<branch>`` opportunistically.
            self._git("fetch", "--no-tags", "origin", branch)
            return True
        except RuntimeError as exc:
            logger.warning("branch scaffold: remote branch lookup failed: %s", exc)
            return False

    def _create_branch(self, branch: str, base: str) -> ScaffoldResult:
        try:
            self._git("checkout", "-B", branch, f"origin/{base}")
        except RuntimeError as exc:
            return ScaffoldResult(
                ok=False, detail=f"could not create {branch}: {exc}"
            )
        detail = f"created {branch} from origin/{base}"
        logger.info("branch scaffold: %s", detail)
        return ScaffoldResult(ok=True, detail=detail, branch=branch)

    def _merge_origin(self, branch: str, base: str) -> ScaffoldResult:  # @stub-protected
        """Merge ``origin/<base>`` into ``branch``; a conflict is data, not a crash."""
        try:
            # A remote-only branch from a previous round is fetched but not
            # checked out yet (a pinned-SHA checkout is detached): the merge
            # must happen on the branch, never on the detached HEAD.
            self._git("checkout", branch)
        except RuntimeError as exc:
            return ScaffoldResult(ok=False, detail=f"could not check out {branch}: {exc}")
        try:
            self._git("merge", "--no-edit", f"origin/{base}")
        except RuntimeError:
            conflicted = self._conflicted_files()
            try:
                self._git("merge", "--abort")
            except RuntimeError as exc:
                logger.warning("branch scaffold: merge abort failed: %s", exc)
            detail = ", ".join(conflicted) or f"merge of origin/{base} into {branch} failed"
            note = (
                f"FIRST: the engine hit a merge conflict with origin/{base}. "
                f"Resolve it as your FIRST action: re-run `git merge origin/{base}`, "
                f"resolve the markers, `git add` + commit, then push."
            )
            logger.info("branch scaffold: merge conflict in %s", detail)
            return ScaffoldResult(
                ok=True, conflict=True, detail=detail, branch=branch, note=note
            )
        detail = f"merged origin/{base} into {branch}"
        logger.info("branch scaffold: %s", detail)
        return ScaffoldResult(ok=True, detail=detail, branch=branch)

    def _conflicted_files(self) -> list[str]:
        """Paths left unmerged by the failed merge (called before the abort)."""
        try:
            out = self._git("diff", "--name-only", "--diff-filter=U")
        except RuntimeError:
            return []
        return [line for line in out.splitlines() if line]

    # ── Plumbing ─────────────────────────────────────────────────

    # @stub-protected
    def _git(self, *args: str) -> str:
        """Run git in the session checkout; a failure raises ``RuntimeError``."""
        # env=_git_subprocess_env(self.env) instead of env={**self.env, "GIT_TERMINAL_PROMPT": "0"}
        raise NotImplementedError
