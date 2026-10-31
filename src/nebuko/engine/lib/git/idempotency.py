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

from collections.abc import Callable, Sequence


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

    def has_base_conflict(self, branch: str, base: str) -> list[str]:  # @stub-protected
        """Dry-run ``git merge-tree --write-tree`` (base first, branch second);
        return the list of conflicted paths (empty list = clean merge). Never
        mutates the worktree.
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
            out = self._run_git(
                [
                    "merge-tree",
                    "--write-tree",
                    "--name-only",
                    "--no-messages",
                    base_ref,
                    branch,
                ]
            )
        except RuntimeError as exc:
            # A merge-level failure (git exit 1, a conflict) writes the tree
            # oid and the conflicted paths to stdout, which ``run_git`` drops
            # when it raises: re-read them directly below. Anything else is an
            # infrastructure "no".
            if " failed (1)" not in str(exc) and "merge-tree" not in str(exc):
                return []
            out = self._merge_tree_stdout(base_ref, branch)
        lines = [line for line in out.splitlines() if line]
        # Line 1 is the written tree oid (present even on conflict); the rest
        # are the conflicted paths.
        return lines[1:] if lines else []

    def _merge_tree_stdout(self, base_ref: str, branch: str) -> str:
        """Raw stdout of the probe, keeping a conflict's rc-1 output readable
        even though ``run_git`` raises instead of returning stdout."""
        import subprocess

        result = subprocess.run(
            ["git", "-C", self._repo_root,
             "merge-tree", "--write-tree", "--name-only", "--no-messages",
             base_ref, branch],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

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
