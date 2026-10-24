"""Engine-owned PR finalizer: open the PR and hand its metadata to the engine.

The ``git-pr-workflow`` skill used to make the agent open its own PR (issue
#1138). The engine now guarantees it after the agent phase: the deterministic
:func:`agent_script.features.ensure_pr.ensure_pr` flow already commits, pushes
and opens a PR idempotently — the finalizer wraps it and returns the PR's
metadata as data, which the engine persists into workflow state
(:class:`PullRequestInfo`).

The agent's own PR wins: ``ensure_pr`` looks the branch's PR up first and is a
no-op when one exists, so an agent that opened a PR itself keeps its branch
name, commit messages and PR body.

Like :class:`nebuko.engine.lib.git.git_session.GitSessionGuard`, the finalizer never
raises: a failure is returned as data and the caller turns it into a run
error — a session whose PR could not be opened must not look green.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    # The lint-config job installs base deps (no openhands) yet must still
    # import this module through the engine package (issue #1129).
    get_logger = logging.getLogger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PrFinalizeResult:
    """Outcome of one PR finalize pass.

    ``ok`` is the pass/fail verdict; ``skipped`` marks the benign case where
    there was nothing to finalize (``ok`` is then True and ``detail`` explains
    why); ``detail`` is actionable prose; ``pr_url`` is the opened or found
    PR, empty when none was opened.
    """

    ok: bool
    skipped: bool = False
    detail: str = ""
    pr_url: str = ""


class PrFinalizer:
    """Open the session's PR through the deterministic fallback flow."""

    # @stub-protected
    def finalize(self) -> PrFinalizeResult:
        """Run the deterministic PR flow and return its outcome as data.

        Returns:
            ``ok=True`` with ``pr_url`` when a PR is open for the branch, a
            benign skip when there was nothing to finalize (no remote,
            detached HEAD, workflow-only delta), and ``ok=False`` with
            actionable detail when the PR could not be opened.
        """
        # Imported lazily: the frozen runtime modules are importable only once
        # the lib directory is on ``sys.path`` (see ``ensure_import_path``).
        from nebuko.engine.paths import ensure_import_path

        ensure_import_path()
        from agent_script.features.ensure_pr.ensure_pr import ensure_pr

        try:
            result = ensure_pr(dry_run=False)
        except Exception as exc:  # noqa: BLE001 - never crash the host
            logger.error("pr finalize: ensure_pr crashed: %s", exc)
            return PrFinalizeResult(ok=False, detail=f"ensure_pr crashed: {exc}")

        if result.error:
            return PrFinalizeResult(ok=False, detail=result.message)
        if result.skipped:
            return PrFinalizeResult(ok=True, skipped=True, detail=result.message)
        if not result.pr_url:
            # A workflow-only delta publishes as a patch comment instead of a
            # PR (issue #1080): nothing to record, not a failure.
            return PrFinalizeResult(
                ok=True, skipped=True, detail=result.message or "no PR opened"
            )

        logger.info("pr finalize: %s", result.message or result.pr_url)
        return PrFinalizeResult(ok=True, detail=result.message, pr_url=result.pr_url)


def pr_number_from_url(pr_url: str) -> int | None:
    """The PR number out of a ``…/pull/<n>`` URL, or ``None``."""
    head, sep, tail = pr_url.rstrip("/").rpartition("/pull/")
    if not (sep and tail.isdigit()):
        return None
    return int(tail)
