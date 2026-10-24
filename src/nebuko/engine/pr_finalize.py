"""Engine-owned PR finalization: the session ends with an open pull request.

The ``git-pr-workflow`` skill used to teach every ``dev`` run to open the PR
itself — find the PR for the branch, create it when missing, and export the
``PR_*`` env vars the state builder reads. That is engine work, not model work
(issue #1138): the PR is mechanical, and the agent's own PR (if any) must win.

:class:`PrFinalizer` closes this, engine-side, mirroring
:class:`~nebuko.engine.git_session.GitSessionGuard`:

* :meth:`PrFinalizer.finalize` runs after the agent phase, before the state is
  persisted. It reuses the deterministic :func:`~ensure_pr.ensure_pr` flow —
  commit/push leftovers, find the PR for the branch, open one when missing —
  which is already idempotent and workflow-patch aware (issue #1080).
* The agent's own PR wins: when the agent already opened one for the branch,
  the finalizer records it instead of opening a duplicate.
* The PR metadata is returned as :class:`~ensure_pr.PullRequestInfo`-shaped
  data and exported as ``PR_NUMBER``/``PR_URL``/``PR_TITLE``/``PR_AUTHOR`` so
  the state builder's ``_build_pr_info`` picks it up (issue #1138).

Both entry points never raise: a failure is returned as data, and the engine
turns it into a run error — an unpublished session must not look green.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from nebuko.engine.paths import ensure_import_path

try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    # The lint-config job installs base deps (no openhands) yet must still
    # import this module through the engine package (issue #1129).
    get_logger = logging.getLogger

logger = get_logger(__name__)

#: Env vars the state builder's ``_build_pr_info`` reads (issue #1138).
# @stub-protected
PR_ENV_VARS: tuple[str, ...] = ("PR_NUMBER", "PR_URL", "PR_TITLE", "PR_AUTHOR")


@dataclass(frozen=True)
class PrFinalizeResult:
    """Outcome of one PR finalization pass.

    ``ok`` is the pass/fail verdict; ``skipped`` marks the benign case where
    there was nothing to finalize (``ok`` is then True and ``detail`` explains
    why); ``pr_url``/``pr_number``/``pr_title`` describe the PR the session
    ended with (empty when skipped or failed); ``detail`` is actionable prose.
    """

    ok: bool
    skipped: bool = False
    pr_url: str = ""
    pr_number: int = 0
    pr_title: str = ""
    detail: str = ""


class PrFinalizer:
    """End the session with an open PR for the session branch.

    Args:
        repo_root: The real checkout (the engine's ``repo_root``).
        env: The run's environment, read for the repo name and the issue
            number, and written for the ``PR_*`` exports.
        issue_number: The triggering issue number (``TARGET_NUMBER`` fallback).
        issue_title: The triggering issue title, used as the PR title when the
            finalizer opens the PR itself.
    """

    # @stub-protected
    def __init__(
        self,
        repo_root: Path,
        env: Mapping[str, str],
        issue_number: int = 0,
        issue_title: str = "",
    ) -> None:
        self.repo_root = Path(repo_root)
        self.env = env
        self.issue_number = issue_number
        self.issue_title = issue_title

    # ── Entry point ───────────────────────────────────────────────

    # @stub-protected
    def finalize(self) -> PrFinalizeResult:
        """Ensure a PR exists for the session branch and export its metadata.

        Returns:
            ``ok=True`` with the PR metadata (an existing PR wins over a new
            one); a benign skip (no remote, detached HEAD, nothing to publish)
            with ``skipped=True``; ``ok=False`` with actionable detail when the
            PR could not be ensured. Never raises.
        """
        ensure_import_path()
        from agent_script.features.ensure_pr.ensure_pr import ensure_pr

        result = ensure_pr()
        if result.error:
            return PrFinalizeResult(ok=False, detail=result.message)
        if result.skipped:
            return PrFinalizeResult(
                ok=True, skipped=True, detail=result.message
            )
        if not result.pr_url:
            # A workflow-only delta publishes as a patch instead of a PR
            # (issue #1080): nothing to record, and not a failure.
            return PrFinalizeResult(
                ok=True, skipped=True, detail="no PR for this session"
            )

        number, title = self._pr_fields(result.pr_url)
        self._export(number, title)
        logger.info("[phase:state] PR finalizer: %s", result.pr_url)
        return PrFinalizeResult(
            ok=True,
            pr_url=result.pr_url,
            pr_number=number,
            pr_title=title,
            detail=result.message,
        )

    # ── Plumbing ──────────────────────────────────────────────────

    def _pr_fields(self, pr_url: str) -> tuple[int, str]:
        """The PR number and title, best-effort from the URL and the issue.

        The number is parsed from the PR URL (``.../pull/<n>``); the title
        falls back to the issue title — the PR the finalizer opens is titled
        after the issue, and the agent's own PR is close enough for the state
        record.
        """
        number = 0
        tail = pr_url.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            number = int(tail)
        title = self.issue_title or f"Work for issue #{self.issue_number or 'n/a'}"
        return number, title

    def _export(self, number: int, title: str) -> None:
        """Export the ``PR_*`` env vars the state builder reads (issue #1138).

        ``build_output._build_pr_info`` reads ``os.environ`` directly, so the
        export targets the process env — the same surface the legacy workflow
        step wrote to.
        """
        os.environ["PR_NUMBER"] = str(number)
        os.environ["PR_URL"] = _pr_url_env(self.env, number)
        os.environ["PR_TITLE"] = title
        os.environ["PR_AUTHOR"] = os.environ.get("GITHUB_ACTOR", "")


def _pr_url_env(env: Mapping[str, str], number: int) -> str:
    """The PR URL for the env export, from the ambient repo when needed."""
    repo = env.get("GITHUB_REPOSITORY", "")
    if repo and number:
        return f"https://github.com/{repo}/pull/{number}"
    return ""