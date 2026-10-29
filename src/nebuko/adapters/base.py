"""The single platform seam of the orchestration engine (issue #590, Stage A).

``PlatformAdapter`` is the port every host satisfies; ``HistoryProvider`` and
``Poster`` are the narrow callables the legacy scripts accept through the seam
refactors. ``nebuko.engine.run_loop`` depends on this module only — no mode branching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from nebuko.engine.models import CommentEntry, Trigger
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

__all__ = [
    "HistoryPage",
    "HistoryProvider",
    "PlatformAdapter",
    "PostedComment",
    "Poster",
    "PosterCapabilities",
]


# @stub-protected
HistoryProvider = Callable[[], Sequence["CommentEntry"]]

# @stub-protected
Poster = Callable[[str], "PostedComment"]


# @stub-protected
class PostedComment(BaseModel):
    """Typed result of a successful ``post()`` (issue #1164)."""

    comment_id: str
    permalink: Optional[str] = None
    anchor: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None


# @stub-protected
class PosterCapabilities(BaseModel):
    """What a platform's comment renderer supports; declared per adapter."""

    model_config = {"frozen": True}

    markdown: bool = True
    details: bool = True
    tasklist: bool = True
    comment_anchor: bool = True
    edit_comment: bool = False
    reactions: bool = False
    history_pagination: bool = False
    raw_html: bool = False


# @stub-protected
class HistoryPage(BaseModel):
    """One page of paginated comment history (``load_history_page``)."""

    entries: list[Any] = Field(default_factory=list)
    next_cursor: Optional[str] = None


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
    def post(self, body: str) -> PostedComment:
        """Publish ``body`` as a comment; return its typed location."""
        ...

    # @stub-protected
    def capabilities(self) -> PosterCapabilities:
        """Declare what this platform's comment renderer supports."""
        ...

    # @stub-protected
    def edit_comment(self, comment_id: str, body: str) -> PostedComment:
        raise NotImplementedError("edit_comment not supported by this adapter")

    # @stub-protected
    def add_reaction(self, comment_id: str, emoji: str) -> None:
        raise NotImplementedError("add_reaction not supported by this adapter")

    # @stub-protected
    def load_history_page(self, cursor: Optional[str] = None) -> HistoryPage:
        raise NotImplementedError("load_history_page not supported by this adapter")

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
