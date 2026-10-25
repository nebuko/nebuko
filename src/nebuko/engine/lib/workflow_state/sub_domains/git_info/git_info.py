"""Engine-owned git sub-domain: branch naming and action history."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class GitActionStatus(str, Enum):
    """Status of one engine git action (branch lifecycle)."""

    CREATE = "create"
    REBASE = "rebase"
    CONFLICT = "conflict"
    DIRTY = "dirty"
    PR = "pr"


class GitHistoryEntry(BaseModel):
    """One engine git action, as recorded in the per-turn history."""

    commit_id: str | None = None
    status: GitActionStatus
    turn: int = 0
    detail: str = ""


class GitInfo(BaseModel):
    """Persisted git state: branch name plus the per-turn action history."""

    branch_name: str | None = None
    pr_number: int | None = None
    history: list[GitHistoryEntry] = Field(default_factory=list)

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
