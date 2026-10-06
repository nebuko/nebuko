"""GitHub platform adapter (issue #590, Stage A).

Composes the existing ``gh``/REST clients behind the protocol;
:meth:`GitHubAdapter.read_trigger` reads ``GITHUB_EVENT_PATH`` and delegates to
``event_normalizer.normalize_trigger``. No new HTTP code.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Sequence

from nebuko.engine.paths import ensure_import_path

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

    from nebuko.engine.models import CommentEntry, Trigger
    from workflow_state.workflow_state import WorkflowState

__all__ = ["GitHubAdapter"]

# Staging directory whose files are uploaded as per-file unzipped CI artifacts
# (``console.log``, ``app.log``). Kept in sync with the upload-artifact steps in
# ``nebuko-workflow.yaml`` (issues #743, #779).
ARTIFACT_DIR_NAME = "nebuko-task-logs"


def _github_client() -> Any:
    ensure_import_path()
    import github_client

    return github_client


# @stub-protected
class GitHubAdapter:  # structurally satisfies adapters.base.PlatformAdapter
    """Adapter composed of the existing ``gh``/REST clients."""

    # @stub-protected
    def __init__(
        self,
        *,
        event_path: Path | None = None,
        repo: str | None = None,
        client: Any | None = None,
    ) -> None:
        from pathlib import Path as _Path

        # Bootstrap the legacy ``engine/lib`` import path once, at adapter
        # construction: every method below imports a legacy module, and the
        # repository packaging does not cover that directory (issue #590
        # round-4, finding 1). Doing it here keeps the methods themselves
        # ordering-safe under the production ``python -m nebuko.engine.run`` bootstrap.
        ensure_import_path()
        self._event_path = _Path(event_path) if event_path is not None else None
        # Mirror ``run_loop.main``'s default so a bare ``GitHubAdapter()`` still
        # resolves the repo from the runner environment instead of failing with
        # an opaque ``None`` deep in the REST client.
        self._repo = repo or os.environ.get("GITHUB_REPOSITORY") or None
        self._client = client
        self._trigger: Trigger | None = None

    # @stub-protected
    def read_trigger(self) -> Trigger:
        from pathlib import Path as _Path

        # Must precede the legacy import: ``event_normalizer`` only resolves
        # once ``engine/lib`` is on ``sys.path`` (issue #590 round-4,
        # finding 1 — ``__init__`` also bootstraps, this keeps the method
        # correct on its own if it is ever called on a bare instance).
        ensure_import_path()
        from event_normalizer import normalize_trigger

        path = self._event_path
        if path is None:
            env_path = os.environ.get("GITHUB_EVENT_PATH")
            if not env_path:
                raise RuntimeError("GITHUB_EVENT_PATH is not set")
            path = _Path(env_path)
        payload = json.loads(_Path(path).read_text(encoding="utf-8"))
        trigger = normalize_trigger(payload)
        self._trigger = trigger
        return trigger

    # @stub-protected
    def load_history(self, *, limit: int | None = None) -> Sequence[CommentEntry]:
        comments = self._client_handle().get_issue_comments(self._require_repo(), self._issue_number())
        entries = [self._to_entry(comment) for comment in comments]
        if limit is not None:
            entries = entries[-limit:] if limit > 0 else []
        return entries

    # @stub-protected
    def post(self, body: str) -> str:
        result = self._client_handle().post_comment(self._require_repo(), self._issue_number(), body)
        if isinstance(result, dict):
            return str(result.get("html_url") or result.get("id") or "")
        return str(result or "")

    # @stub-protected
    def load_state(self) -> WorkflowState | None:
        from resolve_config.features.fetch_state.fetch_state import extract_state

        return extract_state(self.load_history())

    # @stub-protected
    def save_state(self, state: WorkflowState) -> None:
        from workflow_state.state_codec import WorkflowStateCodec

        WorkflowStateCodec().save_prev(state)

    # @stub-protected
    def create_issue(self, *, title: str, body: str, labels: Sequence[str] = ()) -> int:
        from issue_creation import GitHubClient

        result = GitHubClient(self._require_repo()).create_issue(title, body, list(labels))
        if not result:
            raise RuntimeError(f"Failed to create issue: {title!r}")
        return int(result["number"])

    # @stub-protected
    def artifact_dir(self) -> Path:
        from pathlib import Path as _Path

        base = _Path(os.environ.get("RUNNER_TEMP", "/tmp"))
        return base / ARTIFACT_DIR_NAME

    # @stub-protected
    def write_artifact(self, name: str, content: str) -> None:
        directory = self.artifact_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(content, encoding="utf-8")

    # ── internals ────────────────────────────────────────────────────

    def _require_repo(self) -> str:
        if not self._repo:
            raise RuntimeError(
                "GitHubAdapter requires a repository: pass repo=... or set GITHUB_REPOSITORY"
            )
        return self._repo

    def _client_handle(self) -> Any:
        return self._client if self._client is not None else _github_client()

    def _issue_number(self) -> int:
        if self._trigger is None:
            self.read_trigger()
        assert self._trigger is not None  # read_trigger always sets it
        return self._trigger.number

    @staticmethod
    def _to_entry(comment: dict[str, Any]) -> CommentEntry:
        from nebuko.engine.models import CommentEntry

        return CommentEntry(
            author=(comment.get("user") or {}).get("login", ""),
            body=comment.get("body", "") or "",
            created_at=comment.get("created_at", "") or "",
        )
