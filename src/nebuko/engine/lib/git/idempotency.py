"""Content-level idempotency guard for the engine's branch merge (issue #1194).

The engine is stateless between runs, so it asks git before it acts: is the
base already contained in the feature branch? When it is, the merge is
skipped and the run reports the branch as *already up to date*, which tells
the prompt that the previous rounds' work is present.

Most other engine git actions need no guard: a ``git push`` of an up-to-date
branch, a commit with nothing staged (checked by the caller) and a PR lookup
before creation are already safe to repeat. A workflow patch whose change the
base already carries diffs empty and is never delivered.

Like the rest of the git layer, the check never raises for an expected "no".
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from .branch_scaffold import git_subprocess_env


class GitIdempotency:
    """Content-level guard that keeps the branch merge from repeating."""

    def __init__(
        self,
        repo_root: str,
        run_git: Callable[[Sequence[str]], str],
    ) -> None:
        """Bind the guard to one checkout.

        ``run_git`` runs git in ``repo_root`` and raises ``RuntimeError`` on a
        non-zero exit (the ``BranchScaffold._git`` shape).
        """
        self._repo_root = repo_root
        self._run_git = run_git

    def has_base_conflict(
        self, branch: str, base: str
    ) -> list[str] | None:  # @stub-protected
        """Dry-run ``git merge-tree --write-tree`` (base first, branch second);
        return the list of conflicted paths (empty list = clean merge). Never
        mutates the worktree.

        ``None`` when the probe itself fails (unresolvable ref, unrelated
        histories, corrupt repo): "no conflict" must never be guessed from a
        probe that could not run.
        """
        # ``origin/<base>`` needs a remote-tracking ref, which a probe
        # checkout may not have: fall back to the local ``<base>`` ref when
        # the tracking ref is unresolvable.
        base_ref = f"origin/{base}"
        try:
            self._run_git(["rev-parse", "--verify", base_ref])
        except RuntimeError:
            try:
                self._run_git(["rev-parse", "--verify", base, "--"])
                base_ref = base
            except RuntimeError:
                pass
        try:
            return self._merge_tree_paths(base_ref, branch)
        except RuntimeError:
            # Not a conflict: an unresolvable ref, unrelated histories, a
            # corrupt repo — the probe cannot say the merge is clean, so the
            # caller falls back to the unguarded merge.
            return None

    def _merge_tree_paths(self, base_ref: str, branch: str) -> list[str] | None:
        """Conflicted paths out of a read-only ``merge-tree`` probe.

        ``[]`` = clean merge. Git 2.56 emits conflicted paths on stdout on
        rc 1, after the tree oid and before the informational merge messages.
        """
        result = subprocess.run(
            ["git", "-C", self._repo_root,
             "merge-tree", "--write-tree", "--name-only",
             base_ref, branch],
            capture_output=True,
            text=True,
            env=git_subprocess_env({}),
        )
        if result.returncode not in (0, 1):
            # Not a conflict: an unresolvable ref, unrelated histories, a
            # corrupt repo — the probe cannot answer, so the caller falls
            # back to the unguarded merge instead of trusting a clean read.
            raise RuntimeError(
                f"git merge-tree {base_ref} {branch} failed: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        if result.returncode == 1:
            # Exit 1 means a conflict; the rc-1 stdout carries the tree oid,
            # the conflicted paths, then the informational merge messages.
            lines = [line for line in result.stdout.splitlines() if line]
            # Line 1 is the written tree oid; the conflicted paths follow, up
            # to the blank line before the informational merge messages.
            return lines[1:lines.index("")] if "" in lines else lines[1:]
        return []

    def base_already_merged(self, branch: str, base: str) -> bool:  # @stub-protected
        """Whether ``branch`` already contains ``origin/<base>``.

        ``git merge-base --is-ancestor`` exits 0 when the base is an ancestor
        of the branch — the merge would be a no-op and must not run.
        """
        try:
            self._run_git(["merge-base", "--is-ancestor", f"origin/{base}", branch])
        except RuntimeError:
            return False
        return True
