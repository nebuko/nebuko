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
