"""Content-level idempotency guards for engine git actions (issue #1194).

The engine used to merge ``origin/<base>`` into the feature branch on every
issue-triggered run, even when the base had not moved. That redundant merge
made already-committed work look absent, and the agent re-applied a patch
that was already in history. The guards here answer the two questions the
engine must ask before it acts:

- is the base already contained in the branch (skip the merge)?
- is this patch already present in the tree (skip the delivery)?

Like the rest of the git layer, the checks never raise for an expected
"no": a failed ``--check`` probe is a ``False`` answer, not an error.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence


class GitIdempotency:
    """Content-level guards that keep engine git actions from repeating."""

    def __init__(
        self,
        repo_root: str,
        run_git: Callable[[Sequence[str]], str],
    ) -> None:
        """Bind the guards to one checkout.

        ``run_git`` runs git in ``repo_root`` and raises ``RuntimeError`` on a
        non-zero exit (the ``BranchScaffold._git`` / ``ensure_pr._run_git``
        shape).
        """
        self._repo_root = repo_root
        self._run_git = run_git

    def base_already_merged(self, branch: str, base: str) -> bool:  # @stub-protected
        """Whether ``branch`` already contains ``origin/<base>``.

        ``git merge-base --is-ancestor`` exits 0 when the base is an ancestor
        of the branch — the merge would be a no-op and must not run.
        """
        try:
            self._run_git(
                ["merge-base", "--is-ancestor", f"origin/{base}", branch]
            )
            return True
        except RuntimeError:
            return False

    def patch_already_applied(self, patch_text: str) -> bool:  # @stub-protected
        """Whether the patch is already present in the working tree.

        ``git apply --check --reverse`` succeeds exactly when the diff can be
        reversed out of the tree — i.e. the change is already applied.
        """
        return self._check_patch(patch_text, reverse=True)

    def patch_applies_cleanly(self, patch_text: str) -> bool:  # @stub-protected
        """Whether the patch applies cleanly to the working tree."""
        return self._check_patch(patch_text, reverse=False)

    def _check_patch(self, patch_text: str, *, reverse: bool) -> bool:
        """Dry-run ``git apply`` against the tree; a failed check is ``False``.

        The patch goes through a temp file inside the checkout (``git apply``
        resolves relative paths against the repo root), so the guard works
        with any ``run_git`` that takes arguments only.
        """
        if not patch_text.strip():
            return False
        fd, path = tempfile.mkstemp(dir=self._repo_root, suffix=".patch")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    patch_text if patch_text.endswith("\n") else patch_text + "\n"
                )
            args = ["apply", "--check"]
            if reverse:
                args.append("--reverse")
            args.append(os.path.basename(path))
            self._run_git(args)
            return True
        except RuntimeError:
            return False
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
