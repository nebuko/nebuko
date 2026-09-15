"""Shared engine models (Stage A of #585).

Defines the adapter-facing value objects ``Trigger`` and ``CommentEntry`` and
re-exports the existing ``WorkflowState`` (WORKFLOW_STATE v1 — the module is
imported, never copied or modified; VERSION stays 1).
"""

from __future__ import annotations

import sys

from pydantic import BaseModel

from . import SCRIPTS_DIR

# Make the existing pipeline scripts importable (their own modules use
# relative sibling imports, so the scripts dir itself must be on sys.path).
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Re-export the existing WORKFLOW_STATE v1 model (do NOT copy or modify it).
from workflow_state.workflow_state import WorkflowState  # noqa: E402

__all__ = ["CommentEntry", "Trigger", "WorkflowState"]


class Trigger(BaseModel):
    """Normalized inbound trigger for one engine run.

    Mirrors the KEY=value contract of `event_normalizer.py`
    (TARGET_NUMBER, TRIGGER_BODY, TRIGGER_AUTHOR, EVENT_TYPE) as a typed value.
    """

    author: str = ""
    body: str = ""
    type: str = ""
    number: int = 0


class CommentEntry(BaseModel):
    """One history entry as seen by guards, state extraction, and context building.

    ``created_at`` is an ISO-8601 string. The ``body`` of adapter entries
    carries the standard markers (``<!-- bot-workflow-terminal -->``,
    ``<!-- bot-agent-* -->``, ``WORKFLOW_STATE`` sections) so the existing
    marker-based logic works unchanged.
    """

    author: str = ""
    body: str = ""
    created_at: str = ""