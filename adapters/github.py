"""GitHub platform adapter (issue #590, Stage A).

Composes the existing ``gh``/REST clients behind the protocol;
:meth:`GitHubAdapter.read_trigger` reads ``GITHUB_EVENT_PATH`` and delegates to
``event_normalizer.normalize_trigger``. No new HTTP code.

Stub step: class skeleton only. Bodies are materialised by @@dev in the
implement step.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Sequence

from engine.paths import ensure_import_path

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

    from engine.models import CommentEntry, Trigger
    from workflow_state.workflow_state import WorkflowState

__all__ = ["GitHubAdapter"]


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

        self._event_path = _Path(event_path) if event_path is not None else None
        self._repo = repo
        self._client = client
        self._trigger: Trigger | None = None

    # @stub-protected
    def read_trigger(self) -> Trigger:
        from pathlib import Path as _Path

        from event_normalizer import normalize_trigger

        ensure_import_path()
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
        comments = self._client_handle().get_issue_comments(self._repo, self._issue_number())
        entries = [self._to_entry(comment) for comment in comments]
        if limit is not None:
            entries = entries[-limit:] if limit > 0 else []
        return entries

    # @stub-protected
    def post(self, body: str) -> str:
        result = self._client_handle().post_comment(self._repo, self._issue_number(), body)
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

        result = GitHubClient(self._repo or "").create_issue(title, body, list(labels))
        if not result:
            raise RuntimeError(f"Failed to create issue: {title!r}")
        return int(result["number"])

    # @stub-protected
    def write_artifact(self, name: str, content: str) -> None:
        from pathlib import Path as _Path

        path = _Path(os.environ.get("RUNNER_TEMP", "/tmp")) / name
        path.write_text(content, encoding="utf-8")

    # ── internals ────────────────────────────────────────────────────

    def _client_handle(self) -> Any:
        return self._client if self._client is not None else _github_client()

    def _issue_number(self) -> int:
        if self._trigger is None:
            self.read_trigger()
        assert self._trigger is not None  # read_trigger always sets it
        return self._trigger.number

    @staticmethod
    def _to_entry(comment: dict[str, Any]) -> CommentEntry:
        from engine.models import CommentEntry

        return CommentEntry(
            author=(comment.get("user") or {}).get("login", ""),
            body=comment.get("body", "") or "",
            created_at=comment.get("created_at", "") or "",
        )
