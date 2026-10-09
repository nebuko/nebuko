"""Comment recency-window entity (ADR-0003, issues #683/#844).

Single source of truth for how many recent thread comments the engine injects
into an agent prompt. The size previously lived only in the reusable GitHub
Actions workflow (``history_comment_count``, default 5) and never reached
Python: ``Engine.history_limit`` was not wired, so the whole thread was injected
(issue #844). Owning the default, the env resolution and the normalization here
keeps a "declared but unwired" cap from recurring.
"""

from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel, ConfigDict

DEFAULT_HISTORY_COMMENT_COUNT = 5
HISTORY_COMMENT_COUNT_ENV = "HISTORY_COMMENT_COUNT"


class HistoryWindowConfig(BaseModel):
    """Recency-window size for comment injection (ADR-0003)."""

    history_comment_count: int = DEFAULT_HISTORY_COMMENT_COUNT

    model_config = ConfigDict(frozen=True)

    def normalized_count(self) -> int:
        """A usable window size: a non-positive value falls back to the default.

        Normalization guarantees every caller receives a positive bound — no
        declared value yields unbounded injection.
        """
        if self.history_comment_count > 0:
            return self.history_comment_count
        return DEFAULT_HISTORY_COMMENT_COUNT

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "HistoryWindowConfig":
        """Resolve the window from ``HISTORY_COMMENT_COUNT``.

        Absent, empty, or unparseable input yields the default instance; a
        parseable integer is kept verbatim (``normalized_count`` bounds it).
        """
        raw = (env.get(HISTORY_COMMENT_COUNT_ENV) or "").strip()
        if not raw:
            return cls()
        try:
            count = int(raw)
        except (TypeError, ValueError):
            return cls()
        return cls(history_comment_count=count)
