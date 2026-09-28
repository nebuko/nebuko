"""Spec tests for the history-window selection policy (issue #683).

Covers the plan's edge cases (§5):

- default ``5`` when the value is omitted / non-numeric / negative
- explicit override wins
- ``0`` → pinned only
- more pinned than N → all pinned still injected
- pinned comment inside the last-N window → de-duplicated (appears once)
- fewer comments than N → inject all available
- a single deterministic chronological order (last-N-overall ∪ pinned)
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from features.select_history_comments.history import (
    HistorySelection,
    HistoryWindowConfig,
    select_history,
)


def _comment(cid: int, body: str = "") -> dict:
    return {"id": cid, "body": body, "user": {"login": f"user{cid}"}}


def _ids(comments) -> list:
    return [c["id"] for c in comments]


class TestHistoryWindowConfigNormalizedCount(unittest.TestCase):
    """normalized_count() clamping/fallback behaviour."""

    def test_default_is_five(self):
        self.assertEqual(HistoryWindowConfig().normalized_count(), 5)

    def test_explicit_value_is_preserved(self):
        self.assertEqual(
            HistoryWindowConfig(history_comment_count=3).normalized_count(), 3
        )

    def test_zero_is_allowed(self):
        self.assertEqual(
            HistoryWindowConfig(history_comment_count=0).normalized_count(), 0
        )

    def test_negative_falls_back_to_default(self):
        self.assertEqual(
            HistoryWindowConfig(history_comment_count=-2).normalized_count(), 5
        )

    def test_non_numeric_falls_back_to_default(self):
        self.assertEqual(
            HistoryWindowConfig(history_comment_count="abc").normalized_count(), 5
        )


class TestSelectHistory(unittest.TestCase):
    """Windowing + pinned union semantics."""

    def test_fewer_comments_than_n_returns_all(self):
        comments = [_comment(1), _comment(2), _comment(3)]
        result = select_history(comments, [], HistoryWindowConfig(5))
        self.assertEqual(_ids(result.all_comments()), [1, 2, 3])

    def test_last_n_overall_comments_selected(self):
        comments = [_comment(i) for i in range(1, 9)]
        result = select_history(comments, [], HistoryWindowConfig(3))
        self.assertEqual(_ids(result.all_comments()), [6, 7, 8])

    def test_window_is_last_n_overall_regardless_of_pinned(self):
        """N counts every comment, so a pinned comment inside the window consumes a slot."""
        comments = [_comment(i) for i in range(1, 6)]
        result = select_history(comments, ["4"], HistoryWindowConfig(2))
        self.assertEqual(_ids(result.all_comments()), [4, 5])

    def test_pinned_outside_window_added_chronologically(self):
        """A pinned comment before the window is merged into chronological order."""
        comments = [_comment(i) for i in range(1, 6)]
        result = select_history(comments, ["1"], HistoryWindowConfig(2))
        self.assertEqual(_ids(result.all_comments()), [1, 4, 5])

    def test_pinned_inside_window_with_trailing_comments(self):
        """Pinned inside the window plus newer comments: single chronological order."""
        comments = [_comment(i) for i in range(1, 6)]
        result = select_history(comments, ["2"], HistoryWindowConfig(3))
        self.assertEqual(_ids(result.all_comments()), [2, 3, 4, 5])

    def test_selection_exposes_resolved_window_fields(self):
        comments = [_comment(i) for i in range(1, 6)]
        result = select_history(comments, ["1"], HistoryWindowConfig(2))
        self.assertEqual(result.requested_count, 2)
        self.assertEqual(_ids(result.recent_comments), [4, 5])
        self.assertEqual(_ids(result.pinned_comments), [1])

    def test_zero_injects_pinned_only(self):
        comments = [_comment(1), _comment(2), _comment(3)]
        result = select_history(comments, ["3"], HistoryWindowConfig(0))
        self.assertEqual(_ids(result.all_comments()), [3])

    def test_more_pinned_than_n_still_all_injected(self):
        comments = [_comment(1), _comment(2), _comment(3), _comment(4)]
        result = select_history(comments, ["1", "2", "3"], HistoryWindowConfig(1))
        selected = _ids(result.all_comments())
        for cid in (1, 2, 3):
            self.assertIn(cid, selected)

    def test_pinned_inside_window_is_deduplicated(self):
        comments = [_comment(1), _comment(2), _comment(3), _comment(4)]
        result = select_history(comments, ["4"], HistoryWindowConfig(2))
        selected = _ids(result.all_comments())
        self.assertEqual(selected.count(4), 1)
        self.assertIn(4, selected)

    def test_chronological_order_preserved(self):
        comments = [_comment(1), _comment(2), _comment(3), _comment(4), _comment(5)]
        result = select_history(comments, ["1"], HistoryWindowConfig(2))
        self.assertEqual(_ids(result.all_comments()), [1, 4, 5])


if __name__ == "__main__":
    unittest.main()
