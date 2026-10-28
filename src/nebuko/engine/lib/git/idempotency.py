"""Content-level idempotency guards for engine git actions.

The engine used to re-apply already-committed work after a base update: the
branch scaffold merged ``origin/<base>`` unconditionally and nothing compared
patch content against the tree. :class:`GitIdempotency` is the shared guard
both call sites use — an ancestor check for the merge no-op and a reverse
``git apply --check`` for a patch that is already present.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence


class GitIdempotency:
    """Content-level guards that keep engine git actions from repeating."""

    _repo_root: str
    _run_git: Callable[[Sequence[str]], str]  # raises RuntimeError on non-zero exit

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, repo_root: str, run_git: Callable[[Sequence[str]], str]) -> None:
        self._repo_root = repo_root
        self._run_git = run_git

    # @stub-protected: signature defined by architect — do not modify
    def base_already_merged(self, branch: str, base: str) -> bool:
        """Whether ``branch`` already contains ``origin/<base>``.

        Runs ``git merge-base --is-ancestor origin/<base> <branch>``: exit 0
        means the base is contained and a merge would be a no-op.
        """
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def patch_already_applied(self, patch_text: str) -> bool:
        """Whether ``patch_text`` is already present in the tree.

        Runs ``git apply --check --reverse -`` with the patch on stdin: a
        clean reverse-apply means the change is already there.
        """
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def patch_applies_cleanly(self, patch_text: str) -> bool:
        """Whether ``patch_text`` would apply cleanly (forward ``--check``)."""
        raise NotImplementedError
