"""Comment recency-window sub-domain (ADR-0003)."""

from .history_window import (
    DEFAULT_HISTORY_COMMENT_COUNT,
    HISTORY_COMMENT_COUNT_ENV,
    HistoryWindowConfig,
)

__all__ = [
    "DEFAULT_HISTORY_COMMENT_COUNT",
    "HISTORY_COMMENT_COUNT_ENV",
    "HistoryWindowConfig",
]
