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

__all__ = ["GitHubAdapter", "PublishError"]

# Staging directory whose files are uploaded as per-file unzipped CI artifacts
# (``console.txt``, ``app.txt``). Kept in sync with the upload-artifact steps in
# ``nebuko-workflow.yaml`` (issues #743, #779, #894).
ARTIFACT_DIR_NAME = "nebuko-task-logs"

# Durable copy of a comment body whose write could not be delivered (issue
# #805), staged next to the run logs by ``run_loop._publish_fallback``. Wiring
# the matching ``upload-artifact`` step into ``nebuko-workflow.yaml`` needs
# ``workflows`` push permission and is tracked as remaining work in PR #814.
COMMENT_BODY_ARTIFACT = "comment-body.md"


# @stub-protected
class PublishError(RuntimeError):
    """A comment write could not be delivered after the transport's retries.

    Raised instead of silently returning an empty id, so ``run_loop``'s publish
    phase fails loudly rather than reporting a green run whose deliverable
    vanished (issue #805).
    """

    # @stub-protected
    def __init__(self, detail: str, error_kind: Any | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_kind = error_kind


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
        # One bounded read of the thread per adapter instance. ``load_history``
        # and ``load_state`` share it instead of re-paginating the whole issue
        # history several times per run (issue #805, Pillar C).
        self._snapshot: Any | None = None
        self._snapshot_limit: int | None = None

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
        if limit is not None:
            # The first explicit bound wins the single fetch; a later
            # ``load_state`` then reuses the same snapshot.
            self._snapshot_limit = limit
        entries = [self._to_entry(comment) for comment in self._thread_snapshot().comments]
        if limit is not None:
            entries = entries[-limit:] if limit > 0 else []
        return entries

    # @stub-protected
    def load_raw_thread(self) -> tuple[Any, Sequence[dict[str, Any]]]:
        """Full ``(issue, comments)`` payloads for the thread ledger (issue #872).

        The ledger reconciles the *whole* thread (so a vanished id is safely
        detected as deleted), not the bounded recency snapshot. The snapshot is
        reused when it was already fetched unbounded; otherwise the full list is
        fetched once. The issue object is best-effort (``None`` on failure).
        """
        client = self._client_handle()
        repo = self._require_repo()
        number = self._issue_number()

        snapshot = self._thread_snapshot()
        comments: Sequence[dict[str, Any]] = snapshot.comments
        if self._snapshot_limit is not None:
            comments = client.get_issue_comments(repo, number)

        getter = getattr(client, "get_issue", None)
        issue = None
        if getter is not None:
            try:
                issue = getter(repo, number)
            except Exception:  # noqa: BLE001 - best-effort metadata
                issue = None
        return issue, comments

    # @stub-protected
    def _thread_snapshot(self) -> Any:
        """Return the cached per-run snapshot, fetching the thread at most once."""
        if self._snapshot is None:
            from thread_snapshot import STATE_TAIL_FLOOR, build_snapshot

            limit = self._snapshot_limit
            if limit is not None:
                limit = max(limit, STATE_TAIL_FLOOR)
            self._snapshot = self._fetch_snapshot(limit, build_snapshot)
        return self._snapshot

    def _fetch_snapshot(self, limit: int | None, build_snapshot: Any) -> Any:
        client = self._client_handle()
        repo = self._require_repo()
        number = self._issue_number()
        typed = getattr(client, "fetch_issue_comments", None)
        if typed is not None:
            result = typed(repo, number, limit=limit)
            if getattr(result, "ok", True):
                return build_snapshot(result.comments)
            return build_snapshot((), fetch_error=getattr(result, "detail", ""))
        # Injected/legacy clients expose only the list-returning function.
        return build_snapshot(client.get_issue_comments(repo, number, limit=limit))

    # @stub-protected
    def post(self, body: str) -> str:
        """Publish ``body``; raise :class:`PublishError` if it cannot be delivered.

        The client already retries transient/rate-limit failures; a remaining
        failure is terminal for the write and must surface, never be coerced to
        an empty id (issue #805).
        """
        result = self._client_handle().post_comment(
            self._require_repo(), self._issue_number(), body
        )
        ok = getattr(result, "ok", None)
        if ok is False:
            raise PublishError(
                str(getattr(result, "detail", "") or "comment write failed"),
                getattr(result, "error_kind", None),
            )
        if isinstance(result, dict):
            return str(result.get("html_url") or result.get("id") or "")
        url = getattr(result, "comment_url", "") or getattr(result, "comment_id", "")
        return str(url or "")

    # @stub-protected
    def load_state(self) -> WorkflowState | None:
        # Reads the shared snapshot (one bounded fetch per run) instead of
        # re-listing the whole thread a second time (issue #805, Pillar C).
        return self._thread_snapshot().state

    # @stub-protected
    def save_state(self, state: WorkflowState) -> None:
        from workflow_state.state_codec import WorkflowStateCodec

        WorkflowStateCodec().save_prev(state)

    # @stub-protected
    def delete_issue_comments(self, issue: str, from_comment: str = "") -> int:
        client = self._client_handle()
        repo = self._require_repo()
        comments = client.get_issue_comments(repo, int(issue))
        boundary = int(from_comment) if str(from_comment).strip().isdigit() else None
        deleted = 0
        for comment in comments:
            comment_id = comment.get("id")
            if comment_id is None:
                continue
            if boundary is not None and int(comment_id) < boundary:
                continue
            if client.delete_comment(repo, int(comment_id)):
                deleted += 1
        return deleted

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

        raw_id = comment.get("id")
        return CommentEntry(
            author=(comment.get("user") or {}).get("login", ""),
            body=comment.get("body", "") or "",
            created_at=comment.get("created_at", "") or "",
            comment_id=str(raw_id) if raw_id is not None else None,
        )
