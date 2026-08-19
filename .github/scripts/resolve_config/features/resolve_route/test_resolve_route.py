"""Tests for resolve_route feature (routing policy helpers)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.features.resolve_route.resolve_route import (  # noqa: E402
    MAX_LOOP_COUNT,
    count_consecutive_same_agent,
)


class TestCountConsecutiveSameAgent:
    def test_counts_trailing_run(self):
        from workflow_state import HistoryEntry
        history = [
            HistoryEntry(step="s1", agent="genius"),
            HistoryEntry(step="s2", agent="dev"),
            HistoryEntry(step="s3", agent="dev"),
            HistoryEntry(step="s4", agent="dev"),
        ]
        assert count_consecutive_same_agent(history, "dev") == 3

    def test_no_run(self):
        from workflow_state import HistoryEntry
        history = [HistoryEntry(step="s1", agent="genius")]
        assert count_consecutive_same_agent(history, "dev") == 0

    def test_empty_history(self):
        assert count_consecutive_same_agent([], "dev") == 0

    def test_max_loop_count_safeguard(self):
        assert MAX_LOOP_COUNT == 5