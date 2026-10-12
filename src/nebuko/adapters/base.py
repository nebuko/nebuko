"""The single platform seam of the orchestration engine (issue #590, Stage A).

``PlatformAdapter`` is the port every host satisfies; ``HistoryProvider`` and
``Poster`` are the narrow callables the legacy scripts accept through the seam
refactors. ``nebuko.engine.run_loop`` depends on this module only — no mode branching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from nebuko.engine.models import CommentEntry, Trigger
    from workflow_state.workflow_state import WorkflowState

__all__ = ["HistoryProvider", "Poster", "PlatformAdapter"]


# @stub-protected
HistoryProvider = Callable[[], Sequence["CommentEntry"]]

# @stub-protected
Poster = Callable[[str], str]


# @stub-protected
@runtime_checkable
class PlatformAdapter(Protocol):
    """Port describing everything the engine needs from a hosting platform."""

    # @stub-protected
    def read_trigger(self) -> Trigger:
        """Normalize the platform event into a :class:`Trigger`."""
        ...

    # @stub-protected
    def load_history(self, *, limit: int | None = None) -> Sequence[CommentEntry]:
        """Return the platform comment history, oldest first."""
        ...

    # @stub-protected
    def post(self, body: str) -> str:
        """Publish ``body`` as a comment; return its id/url."""
        ...

    # @stub-protected
    def load_state(self) -> WorkflowState | None:
        """Return the persisted ``WORKFLOW_STATE`` v1, or ``None``."""
        ...

    # @stub-protected
    def save_state(self, state: WorkflowState) -> None:
        """Persist ``state`` for the next run."""
        ...

    # @stub-protected
    def delete_issue_comments(self, issue: str, from_comment: str = "") -> int:
        """Delete every comment on the issue, or ``from_comment`` and all later
        ones, and return the number deleted."""
        ...

    # @stub-protected
    def artifact_dir(self) -> Path:
        """The host directory where a run's artifacts are staged."""
        ...

    # @stub-protected
    def write_artifact(self, name: str, content: str) -> None:
        """Write a run artifact (``latest_answer.md``, ``console.txt``, ...)."""
        ...
