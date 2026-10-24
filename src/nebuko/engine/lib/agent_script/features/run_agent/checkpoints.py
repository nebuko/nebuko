"""Checkpoint schedule — multi-fire timers derived from the soft budget (issue #966).

The single soft-kill watchdog is one event: the in-flight call is discarded and
everything the agent has not written down is lost. A checkpoint schedule fires
*before* the budget is gone (50% / 75% / 100%), so the run produces a resumable
handoff record while it still has time to act on it.

Pure standard library; unit-testable with short delays (mirrors
``run_watchdog.py``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from openhands.sdk import get_logger

logger = get_logger(__name__)

#: Fractions of ``soft_timeout_s`` at which the checkpoints fire.
CHECKPOINT_FRACTIONS: tuple[float, float, float] = (0.5, 0.75, 1.0)


@dataclass(frozen=True)
class CheckpointSchedule:
    """Absolute-from-arm checkpoint delays (seconds), computed from the soft budget."""

    delays_s: tuple[float, ...]  # @stub-protected

    @classmethod
    def from_soft_timeout(cls, soft_timeout_s: float) -> "CheckpointSchedule":  # @stub-protected
        """delays_s == tuple(f * soft_timeout_s for f in CHECKPOINT_FRACTIONS)."""
        raise NotImplementedError

    def delay(self, index: int) -> float | None:  # @stub-protected
        """Delay for 0-based index; None past the end."""
        raise NotImplementedError

    def __len__(self) -> int:  # @stub-protected
        raise NotImplementedError


class CheckpointScheduler:
    """Sequential daemon timers; fires on_checkpoint(index) at each absolute delay from arm()."""

    def __init__(self, schedule: CheckpointSchedule, on_checkpoint) -> None:  # @stub-protected
        raise NotImplementedError

    def arm(self) -> "CheckpointScheduler":  # @stub-protected
        raise NotImplementedError

    def cancel(self) -> None:  # @stub-protected
        raise NotImplementedError

    @property
    def fired_count(self) -> int:  # @stub-protected
        raise NotImplementedError
