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
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .git_session import GIT_IDENTITY_EMAIL, GIT_IDENTITY_NAME

if TYPE_CHECKING:
    from .idempotency import GitIdempotency

try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    # The lint-config job installs base deps (no openhands) yet must still
    # import this module through the engine package (issue #1129).
    get_logger = logging.getLogger

logger = get_logger(__name__)

#: The default base branch when neither the env nor the remote HEAD names one.
DEFAULT_BASE_BRANCH = "main"

#: Installed by the scaffold: agent commits may not stage workflow files.
WORKFLOW_PRE_COMMIT_HOOK = """#!/bin/sh
# Installed by the nebuko engine (branch scaffold).
staged=$(git diff --cached --name-only -- .github/workflows/)
if [ -n "$staged" ]; then
  echo "nebuko: do not commit .github/workflows/* changes:" >&2
  echo "$staged" >&2
  echo "Unstage them (git restore --staged .github/workflows/) and leave them" >&2
  echo "in the working tree: the engine delivers them as a patch for a human" >&2
  echo "to apply, because the agent token cannot push workflow files." >&2
  exit 1
fi
"""


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
    # True when the branch already contained the base (issue #1194): the
    # merge was skipped, so already-committed work must not look absent.
    already_up_to_date: bool = False
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
    overlay = {
        key: value
        for key, value in (env or {}).items()
        if key.startswith("GIT_")
        or "TOKEN" in key
        or "CREDENTIAL" in key
    }
    return {**os.environ, **overlay, "GIT_TERMINAL_PROMPT": "0"}


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
        from .idempotency import GitIdempotency

        self._idempotency: GitIdempotency = GitIdempotency(
            str(repo_root), self._git_args
        )

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
        # ``actions/checkout`` clones at depth 1: the merge base of a previous
        # round's branch and the base lies past the shallow boundary, and git
        # refuses with "unrelated histories".
        unshallow_problem = self._unshallow()
        if unshallow_problem:
            return ScaffoldResult(ok=False, detail=unshallow_problem)
        result = self._prepare_branch(branch, base)
        if result.ok and result.branch:
            self._install_workflow_hook()
        return result

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
            self._git(
                "fetch", "--no-tags", "origin",
                f"+refs/heads/{base}:refs/remotes/origin/{base}",
            )
            return True
        except RuntimeError as exc:
            logger.warning("branch scaffold: fetch failed: %s", exc)
            return False

    def _unshallow(self) -> str:
        """Fetch the full history of a shallow checkout. Returns a problem."""
        try:
            if self._git("rev-parse", "--is-shallow-repository") != "true":
                return ""
            self._git("fetch", "--no-tags", "--unshallow", "origin")
        except RuntimeError as exc:
            return f"could not unshallow the checkout: {exc}"
        logger.info("branch scaffold: unshallowed the checkout")
        return ""

    def _install_workflow_hook(self) -> None:
        """Refuse agent commits that stage ``.github/workflows/*``.

        The agent token cannot push workflow files, so the engine delivers
        them as a patch (issue #1080). Blocking the commit up front keeps them
        in the working tree, where the publisher captures them, instead of
        forcing a history rewrite later. Best effort: a missing hook only
        means the publisher's rebuild path is taken.
        """
        try:
            hooks_dir = Path(self._git("rev-parse", "--git-path", "hooks"))
            if not hooks_dir.is_absolute():
                hooks_dir = Path(self.repo_root) / hooks_dir
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hook = hooks_dir / "pre-commit"
            hook.write_text(WORKFLOW_PRE_COMMIT_HOOK, encoding="utf-8")
            hook.chmod(0o755)
        except (OSError, RuntimeError) as exc:
            logger.warning("branch scaffold: could not install the hook: %s", exc)

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
        if self._has_local_branch(branch):
            return True
        try:
            if not self._git(
                "ls-remote", "--heads", "origin", f"refs/heads/{branch}"
            ):
                return False
            # The checkout below needs ``refs/remotes/origin/<branch>``.
            self._fetch_branch(branch)
            return True
        except RuntimeError as exc:
            logger.warning("branch scaffold: remote branch lookup failed: %s", exc)
            return False

    def _has_local_branch(self, branch: str) -> bool:
        try:
            return bool(self._git("rev-parse", "--verify", "-q", f"refs/heads/{branch}"))
        except RuntimeError:
            return False

    def _fetch_branch(self, branch: str) -> None:
        """Fetch ``branch`` into its remote-tracking ref (explicit refspec)."""
        self._git(
            "fetch", "--no-tags", "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        )

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
        """Merge ``origin/<base>`` into ``branch``; a conflict is data, not a crash.

        When the branch already contains the base the merge is skipped
        entirely (issue #1194): re-merging an unchanged base made the
        already-committed work look absent and the agent re-applied it.
        """
        try:
            # A remote-only branch from a previous round is fetched but not
            # checked out yet (a pinned-SHA checkout is detached): the merge
            # must happen on the branch, never on the detached HEAD. It is
            # created from the remote ref explicitly: ``git checkout <branch>``
            # only guesses through the configured fetch refspec, which a
            # single-branch (shallow) clone limits to the base.
            if self._has_local_branch(branch):
                self._git("checkout", branch)
            else:
                self._git("checkout", "-b", branch, f"refs/remotes/origin/{branch}")
        except RuntimeError as exc:
            return ScaffoldResult(ok=False, detail=f"could not check out {branch}: {exc}")
        if self._idempotency.base_already_merged(branch, base):
            detail = f"{branch} already contains origin/{base}"
            logger.info("branch scaffold: %s", detail)
            return ScaffoldResult(
                ok=True, already_up_to_date=True, detail=detail, branch=branch
            )
        try:
            # The identity is passed inline: the preflight that writes the
            # repo-local identity may not have run yet, and a runner without a
            # global one cannot create the merge commit.
            self._git(*self._identity_args(), "merge", "--no-edit", f"origin/{base}")
        except RuntimeError as exc:
            conflicted = self._conflicted_files()
            if not conflicted:
                # Not a conflict (unrelated histories, a dirty tree, …): the
                # agent cannot fix it, so the run must fail instead.
                self._abort_merge()
                return ScaffoldResult(
                    ok=False,
                    detail=f"merge of origin/{base} into {branch} failed: {exc}",
                )
            detail = ", ".join(conflicted)
            listed = "\n".join(f"- `{path}`" for path in conflicted)
            if self._engine_runs_from_checkout():
                # The engine imports its own modules lazily from this very
                # tree: conflict markers left on disk crash it mid-run. Keep
                # the tree clean and hand the merge to the agent instead.
                self._abort_merge()
                note = (
                    f"FIRST: merging origin/{base} into `{branch}` conflicts in:\n\n"
                    f"{listed}\n\n"
                    f"Before anything else, run `git merge origin/{base}`, resolve "
                    "every conflict marker, `git add` the files and commit. The "
                    "engine pushes after your run and refuses to publish while "
                    "conflicts remain."
                )
                logger.info("branch scaffold: merge conflict in %s (aborted)", detail)
                return ScaffoldResult(
                    ok=True, conflict=True, detail=detail, branch=branch, note=note
                )
            # The merge is left in progress so the agent resolves the markers
            # in place; the publisher concludes it with the engine commit.
            note = (
                f"FIRST: the engine merged origin/{base} into `{branch}` and the "
                "merge stopped on conflicts. The merge is still in progress. "
                "Resolve it before anything else:\n\n"
                f"{listed}\n\n"
                "Edit each file to remove the conflict markers, then `git add` it. "
                "Do not run `git merge --abort`. Committing is optional: the engine "
                "commits the merge after your run, and refuses to publish if "
                "conflicts remain."
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

    def _engine_runs_from_checkout(self) -> bool:
        """Whether the engine's own source lives inside the session checkout.

        True when nebuko runs on its own repository from the workspace (the
        workflow's ``NEBUKO_ROOT`` is the checkout): every merge then rewrites
        modules the running engine has yet to import.
        """
        try:
            Path(__file__).resolve().relative_to(Path(self.repo_root).resolve())
        except ValueError:
            return False
        return True

    def _abort_merge(self) -> None:
        try:
            self._git("rev-parse", "-q", "--verify", "MERGE_HEAD")
        except RuntimeError:
            return  # the merge never started: nothing to abort
        try:
            self._git("merge", "--abort")
        except RuntimeError as exc:
            logger.warning("branch scaffold: merge abort failed: %s", exc)

    def _identity_args(self) -> list[str]:
        name = self.env.get("NEBUKO_GIT_AUTHOR_NAME") or GIT_IDENTITY_NAME
        email = self.env.get("NEBUKO_GIT_AUTHOR_EMAIL") or GIT_IDENTITY_EMAIL
        return ["-c", f"user.name={name}", "-c", f"user.email={email}"]

    # ── Plumbing ─────────────────────────────────────────────────

    def _git_args(self, args: Sequence[str]) -> str:
        """List-argument adapter over :meth:`_git` for shared helpers."""
        return self._git(*args)

    # @stub-protected
    def _git(self, *args: str) -> str:
        """Run git in the session checkout; a failure raises ``RuntimeError``."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            env=_git_subprocess_env(self.env),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return result.stdout.strip()
