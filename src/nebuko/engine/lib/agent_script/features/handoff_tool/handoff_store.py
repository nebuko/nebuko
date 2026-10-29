"""Engine-side git store for the session handoff file (issue #965).

The handoff tool is called by *any* role, including the read-only ones
(architect / review / po have no ``terminal``/``file_editor``). The git work
therefore happens here, on the engine's side, not in the agent's worktree:
:class:`HandoffStore` writes ``.nebuko/handoff.md``, stages **only** that path
and commits + pushes it.

Staging only the handoff path matters: the git plumbing in ``ensure_pr`` offers
``_commit_pending_changes``, which would sweep the calling agent's in-flight
edits into an engine-authored commit. That is exactly what must not happen.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from openhands.sdk import get_logger

from nebuko.engine.lib.git.git_session import git_identity_args

from agent_script.features.handoff_tool.handoff_document import HandoffDocument
from agent_script.features.ensure_pr.ensure_pr import _run_git

logger = get_logger(__name__)

#: Path of the handoff document, relative to the repository root.
HANDOFF_RELATIVE_PATH = ".nebuko/handoff.md"

#: Commit subject used for every handoff write.
HANDOFF_COMMIT_MESSAGE = "chore: update session handoff"

#: Branch created for a handoff written before any dev branch exists.
HANDOFF_BRANCH_PREFIX = "feature/nebuko-handoff-"

def _timestamp() -> str:
    """UTC timestamp with second resolution (ISO-8601, ``Z`` suffix)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HandoffStore:
    """Read/write the tracked handoff document, committing on the engine's side.

    Args:
        repo_root: Repository root. Falls back to ``GFA_REPO_ROOT`` (the
            in-process engine host runs under a temporary cwd), then to the
            ambient cwd — the same precedence ``ensure_pr._repo_root`` uses.
    """

    def __init__(self, repo_root: str | Path | None = None) -> None:
        if repo_root is None:
            repo_root = os.environ.get("GFA_REPO_ROOT", "").strip() or os.getcwd()
        self._repo_root = str(repo_root)

    # @stub-protected
    def path(self) -> Path:
        """Absolute path of the handoff document."""
        return Path(self._repo_root) / HANDOFF_RELATIVE_PATH

    # @stub-protected
    def load_text(self) -> str:
        """The current handoff text, or ``""`` when the file is absent."""
        try:
            return self.path().read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            return ""
        except OSError as exc:  # pragma: no cover - unreadable path
            logger.warning(f"handoff: cannot read {self.path()}: {exc}")
            return ""

    # @stub-protected
    def current_branch(self) -> str:
        """The currently checked-out branch name (``""`` when detached)."""
        try:
            return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self._repo_root)
        except RuntimeError:
            return ""

    # @stub-protected
    def ensure_branch(self) -> str:
        """Return the branch to commit to, creating one when HEAD is on a base.

        Before the dev step runs there is no feature branch yet (read-only roles
        never create one), so the engine creates and pushes
        ``feature/nebuko-handoff-<UTCts>`` and the dev step reuses it. On a base
        branch the new branch is cut from ``origin/main`` when available, so the
        handoff commit does not carry unrelated local history.
        """
        branch = self.current_branch()
        if branch and branch not in ("HEAD",) and not self._is_base_branch(branch):
            return branch
        new_branch = f"{HANDOFF_BRANCH_PREFIX}{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        start_point = self._base_start_point()
        args = ["checkout", "-b", new_branch]
        if start_point:
            args.append(start_point)
        _run_git(args, cwd=self._repo_root)
        logger.info(f"handoff: created branch {new_branch} (was on '{branch or 'detached HEAD'}')")
        self._push(new_branch)
        return new_branch

    def _is_base_branch(self, branch: str) -> bool:
        """Whether ``branch`` is a base branch (``main``/``master``)."""
        if branch in ("main", "master"):
            return True
        try:
            return bool(_run_git(["config", f"branch.{branch}.nebukoHandoffBase"], cwd=self._repo_root))
        except RuntimeError:
            return False

    def _base_start_point(self) -> str:
        """The freshest base ref to cut the handoff branch from (``""`` if none)."""
        for candidate in ("origin/main", "origin/master"):
            try:
                _run_git(["rev-parse", "--verify", candidate], cwd=self._repo_root)
                return candidate
            except RuntimeError:
                continue
        return ""

    # @stub-protected
    def write_and_commit(self, document: HandoffDocument) -> str:
        """Overwrite the handoff file, commit it, push, and return the hash.

        Only ``HANDOFF_RELATIVE_PATH`` is staged, so the calling agent's other
        edits stay uncommitted and belong to the agent's own commit.

        Args:
            document: The document to render and persist.

        Returns:
            The new commit hash.

        Raises:
            RuntimeError: Any git command failed (surfaced to the agent by the
                tool executor as an error observation).
        """
        branch = self.ensure_branch()
        target = self.path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document.render(), encoding="utf-8")
        _run_git(["add", "--", HANDOFF_RELATIVE_PATH], cwd=self._repo_root)
        staged = _run_git(
            ["diff", "--cached", "--name-only", "--", HANDOFF_RELATIVE_PATH],
            cwd=self._repo_root,
        )
        if not staged.strip():
            # Byte-identical document: nothing to commit, but the branch must
            # still be reachable (it is, we just pushed it, or the file is
            # already tracked here).
            return _run_git(["rev-parse", "HEAD"], cwd=self._repo_root)
        _run_git(
            [*git_identity_args(), "commit", "-m", HANDOFF_COMMIT_MESSAGE, "--", HANDOFF_RELATIVE_PATH],
            cwd=self._repo_root,
        )
        commit = _run_git(["rev-parse", "HEAD"], cwd=self._repo_root)
        self._push(branch)
        return commit

    # @stub-protected
    def restore_from_history(self) -> bool:
        """Revive the last committed version of the handoff into the worktree.

        Called when a human asks for another dev turn *after* the cleanup step
        deleted the file: the next run's prompt can then still carry the
        handoff, and the agent sees it in the worktree. The restore is
        uncommitted (the deletion commit stays in history) and idempotent — a
        present, unmodified file is left alone.

        Returns:
            True when the file was revived into the worktree.
        """
        target = self.path()
        if target.exists():
            try:
                if target.read_text(encoding="utf-8").strip():
                    return False
            except OSError:
                return False
        rev = self._last_commit_touching_handoff()
        if not rev:
            return False
        try:
            # ``rev^:`` — the version *before* the deletion commit. ``rev:``
            # would look the path up in the deletion commit itself, where it is
            # by definition gone.
            content = _run_git(
                ["show", f"{rev}^:{HANDOFF_RELATIVE_PATH}"], cwd=self._repo_root
            )
        except RuntimeError:
            # A root commit has no parent, and a path absent from the parent
            # tree means there is nothing to restore.
            content = ""
        if not content.strip():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        logger.info(f"handoff: restored last deleted version from {rev[:8]}")
        return True

    def _last_commit_touching_handoff(self) -> str:
        """The newest commit that removed the handoff file (``""`` when none)."""
        try:
            out = _run_git(
                [
                    "log",
                    "--diff-filter=D",
                    "--format=%H",
                    "-n",
                    "1",
                    "--",
                    HANDOFF_RELATIVE_PATH,
                ],
                cwd=self._repo_root,
            )
        except RuntimeError:
            return ""
        return out.strip()

    def _push(self, branch: str) -> None:
        """Push ``branch`` to origin, setting the upstream on the first push."""
        remote = "origin"
        try:
            _run_git(["remote", "get-url", remote], cwd=self._repo_root)
        except RuntimeError:
            logger.warning("handoff: no 'origin' remote — skipping push")
            return
        args = ["push", remote, branch]
        try:
            upstream = _run_git(
                ["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
                cwd=self._repo_root,
            )
        except RuntimeError:
            upstream = ""
        if not upstream:
            args.append("--set-upstream")
        try:
            _run_git(args, cwd=self._repo_root)
        except RuntimeError as exc:
            logger.error(f"handoff: push of {branch} failed: {exc}")
            # Re-raised so the tool executor surfaces an error observation: a
            # handoff that never reached the remote must not look persisted
            # (issue #1089).
            raise
