"""Engine-owned git sub-domain: branch naming and action history."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class GitActionStatus(str, Enum):
    """Status of one engine git action (branch lifecycle)."""

    CREATE = "create"
    CONTINUE = "continue"
    CONFLICT = "conflict"
    DIRTY = "dirty"
    PR = "pr"
    # The requested change was already present (issue #1194): no action taken.
    ALREADY_APPLIED = "already_applied"


class GitHistoryEntry(BaseModel):
    """One engine git action, as recorded in the per-turn history."""

    commit_id: str | None = None
    status: GitActionStatus
    turn: int = 0
    detail: str = ""


class GitInfo(BaseModel):
    """Persisted git state: the session branch plus the per-turn action history.

    ``branch_name`` is the single authority for the session branch: the
    evidence checkout (issue #1397) and the failure facts read it instead of a
    duplicate ``current_context`` key. ``base_branch``/``head_sha`` describe
    the branch the engine prepared, and ``created_at`` is when it was first
    recorded — audit data for future stats. The PR itself is
    ``WorkflowState.pull_request``; a second number here would drift.
    """

    branch_name: str | None = None
    base_branch: str | None = None
    head_sha: str | None = None
    created_at: str | None = None
    history: list[GitHistoryEntry] = Field(default_factory=list)

    # @stub-protected
    def record(
        self,
        status: GitActionStatus,
        commit_id: str | None = None,
        turn: int = 0,
        detail: str = "",
    ) -> GitHistoryEntry:
        """Append one engine git action to the history and return it."""
        entry = GitHistoryEntry(
            commit_id=commit_id,
            status=status,
            turn=turn,
            detail=detail,
        )
        self.history.append(entry)
        return entry
