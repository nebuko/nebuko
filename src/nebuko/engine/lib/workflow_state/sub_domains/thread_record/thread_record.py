"""Thread record entity — the workflow-state thread ledger (issue #872).

Every artifact on a GitHub issue (the issue body, ``/nebuko`` commands, engine
notices, agent answers, human feedback) becomes one small, immutable
:class:`ThreadRecord`. The ledger is keyed by the GitHub id of the artifact so a
later run can reconcile the full comment list against it (append-only, dedup by
id, vanished ids flagged) and render the *relevant* flow to the agent instead of
a raw recent-comment tail.

Records hold provenance metadata only — never a copy of the comment body. The
thread itself stays the single source of truth for text; the ledger only says
*what* each artifact is, *who* wrote it, and whether it is trusted enough to
reach the model.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict

__all__ = ["ThreadRecordKind", "ThreadRecord"]


class ThreadRecordKind(str, Enum):  # @stub-protected
    """Classification of one issue artifact for context assembly."""

    ISSUE_BODY = "issue-body"
    COMMAND = "command"
    ENGINE_OUTPUT = "engine-output"
    AGENT_OUTPUT = "agent-output"
    HUMAN_FEEDBACK = "human-feedback"


class ThreadRecord(BaseModel):  # @stub-protected
    """Immutable provenance + classification of one GitHub issue artifact.

    Fields:
        kind: What the artifact is (see :class:`ThreadRecordKind`).
        id: GitHub REST numeric id (issue id or comment id) — the dedup key.
        url: ``html_url`` permalink.
        user_id: GitHub user id of the author (numeric).
        user_login: Author login handle.
        user_name: Author display name (best-effort; the REST comment payload
            does not carry it, so it stays empty in v1).
        created_at: ISO-8601 UTC creation timestamp (ordering + rendering).
        author_association: ``author_association`` from the API.
        trusted: True only for verified human feedback (association allowlist
            **and** trigger-author match) — the gate for rendering.
        deleted: True once the artifact vanished from the thread (kept for
            audit; the renderer skips it).
    """

    model_config = ConfigDict(frozen=True)

    kind: ThreadRecordKind
    id: int
    url: str = ""
    user_id: int = 0
    user_login: str = ""
    user_name: str = ""
    created_at: str = ""
    author_association: str = ""
    trusted: bool = False
    deleted: bool = False
