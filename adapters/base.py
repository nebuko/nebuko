"""PlatformAdapter protocol (Stage A of #585, approved in #585's design thread).

The engine driver depends only on this five-method protocol. Every adapter's
history entries must carry the standard markers (``<!-- bot-workflow-terminal -->``,
``<!-- bot-agent-* -->``, ``WORKFLOW_STATE`` sections) so that `loop_guard`,
`circuit_breaker`, `summarizer_gate`, and state extraction work unchanged.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from engine.models import CommentEntry, Trigger, WorkflowState


@runtime_checkable
class PlatformAdapter(Protocol):
    """Platform seam: trigger in, history/comments out, state round-trip."""

    def read_trigger(self) -> Trigger:
        """Read and normalize the inbound trigger for this run."""
        ...

    def load_history(self) -> list[CommentEntry]:
        """Return the comment history of the target (chronological order)."""
        ...

    def post(self, body: str) -> None:
        """Post a comment body to the target (marker handling is the adapter's job)."""
        ...

    def load_state(self) -> Optional[WorkflowState]:
        """Extract the latest WORKFLOW_STATE v1 snapshot from history, or None."""
        ...

    def save_state(self, state: WorkflowState) -> None:
        """Persist a full-snapshot WORKFLOW_STATE v1 to the target."""
        ...