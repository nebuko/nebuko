"""Session handoff tool package (issue #965)."""

from .handoff_document import (
    HANDOFF_BLOCK_HEADER,
    HandoffDocument,
    render_handoff_block,
)
from .handoff_store import HANDOFF_RELATIVE_PATH, HandoffStore
from .handoff_tool import (
    HANDOFF_DESCRIPTION,
    HandoffAction,
    HandoffExecutor,
    HandoffObservation,
    HandoffTool,
    record_fold,
    reset_registry,
    take_fold,
)

__all__ = [
    "HANDOFF_BLOCK_HEADER",
    "HANDOFF_DESCRIPTION",
    "HANDOFF_RELATIVE_PATH",
    "HandoffAction",
    "HandoffDocument",
    "HandoffExecutor",
    "HandoffObservation",
    "HandoffStore",
    "HandoffTool",
    "record_fold",
    "render_handoff_block",
    "reset_registry",
    "take_fold",
]
