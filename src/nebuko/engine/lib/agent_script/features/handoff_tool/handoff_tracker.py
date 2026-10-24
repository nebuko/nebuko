"""Handoff tracker — run-scoped registry of handoff writes (issue #966).

Records each handoff write (with its ``plan_changes`` count) so the run can
report a drift signal: a plan that never moves across checkpoints suggests
boilerplate status writes. Module-level dict guarded by a lock, same pattern
as the handoff fold registry.
"""

from __future__ import annotations

import time
from threading import Lock

from openhands.sdk import get_logger

logger = get_logger(__name__)


class HandoffTracker:
    """Run-scoped, thread-safe registry of handoff writes and plan-change counts."""

    @staticmethod
    def record_write(conversation_id: str, plan_changes: int) -> None:  # @stub-protected
        raise NotImplementedError

    @staticmethod
    def write_count_since(conversation_id: str, epoch_s: float) -> int:  # @stub-protected
        raise NotImplementedError

    @staticmethod
    def plan_changes_total(conversation_id: str) -> int:  # @stub-protected
        raise NotImplementedError

    @staticmethod
    def take(conversation_id: str) -> tuple[int, int]:  # @stub-protected
        """Pop (write_count, plan_changes_total); returns (0, 0) when absent."""
        raise NotImplementedError
