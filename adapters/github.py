"""GitHub platform adapter (issue #590, Stage A).

Composes the existing ``gh``/REST clients behind the protocol;
:meth:`GitHubAdapter.read_trigger` reads ``GITHUB_EVENT_PATH`` and delegates to
``event_normalizer.normalize_trigger``. No new HTTP code.

Stub step: class skeleton only. Bodies are materialised by @@dev in the
implement step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

    from engine.models import CommentEntry, Trigger
    from workflow_state.workflow_state import WorkflowState

__all__ = ["GitHubAdapter"]


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
        raise NotImplementedError

    # @stub-protected
    def read_trigger(self) -> Trigger:
        raise NotImplementedError

    # @stub-protected
    def load_history(self, *, limit: int | None = None) -> Sequence[CommentEntry]:
        raise NotImplementedError

    # @stub-protected
    def post(self, body: str) -> str:
        raise NotImplementedError

    # @stub-protected
    def load_state(self) -> WorkflowState | None:
        raise NotImplementedError

    # @stub-protected
    def save_state(self, state: WorkflowState) -> None:
        raise NotImplementedError

    # @stub-protected
    def create_issue(self, *, title: str, body: str, labels: Sequence[str] = ()) -> int:
        raise NotImplementedError

    # @stub-protected
    def write_artifact(self, name: str, content: str) -> None:
        raise NotImplementedError
