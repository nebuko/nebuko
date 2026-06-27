import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
from workflow_state import STATE_MARKER_START, STATE_MARKER_END
from workflow_state.features.get_last_state.get_last_state import (
    extract_latest_state_from_comments,
    _enrich_history_with_comment_ids,
)


def _make_state_json(history_entries=None, version=1):
    """Build a minimal WorkflowState JSON dict."""
    return json.dumps({
        "version": version,
        "workflow": "default",
        "step": "analyze",
        "agent": "genius",
        "phase": "analysis",
        "history": history_entries or [],
    })


def _make_comment(body: str, comment_id: int) -> dict:
    """Wrap a body string in a comment dict with an id."""
    return {"id": comment_id, "body": body}


def _make_state_comment(comment_id: int, history_entries=None, version=1) -> dict:
    """Create a comment containing a valid state block."""
    json_str = _make_state_json(history_entries, version)
    body = f"{STATE_MARKER_START}\n{json_str}\n{STATE_MARKER_END}"
    return _make_comment(body, comment_id)


class TestEnrichHistoryWithCommentIds(unittest.TestCase):
    """Test the enrichment helper function."""

    def test_enriches_matching_entries(self):
        entry_a = HistoryEntry(step="analyze", agent="genius")
        entry_b = HistoryEntry(step="plan", agent="genius")
        history = [entry_a, entry_b]
        state_comments = [{"id": 123}, {"id": 456}]

        _enrich_history_with_comment_ids(history, state_comments)

        self.assertEqual(entry_a.comment_id, 123)
        self.assertEqual(entry_b.comment_id, 456)

    def test_leaves_trailing_none_when_fewer_comments(self):
        entry_a = HistoryEntry(step="analyze", agent="genius")
        entry_b = HistoryEntry(step="plan", agent="genius")
        history = [entry_a, entry_b]
        state_comments = [{"id": 123}]

        _enrich_history_with_comment_ids(history, state_comments)

        self.assertEqual(entry_a.comment_id, 123)
        self.assertIsNone(entry_b.comment_id)

    def test_empty_history_is_unchanged(self):
        state_comments = [{"id": 123}]
        _enrich_history_with_comment_ids([], state_comments)  # No-op

    def test_no_state_comments_leaves_none(self):
        entry = HistoryEntry(step="analyze", agent="genius")
        _enrich_history_with_comment_ids([entry], [])
        self.assertIsNone(entry.comment_id)


class TestExtractLatestStateFromComments(unittest.TestCase):
    """Test comment_id enrichment via extract_latest_state_from_comments."""

    def test_single_state_comment_enriches_history(self):
        """Agent B reads Agent A's comment → history[0].comment_id is set."""
        entry_a = {"step": "analyze", "agent": "genius", "summary": "Analysis done"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)

        result = extract_latest_state_from_comments([comment_123])

        self.assertIsNotNone(result)
        self.assertEqual(len(result.history), 1)
        self.assertEqual(result.history[0].comment_id, 123)

    def test_multiple_state_comments_enrich_all_history(self):
        """Agent C reads Agent A and B's comments → both history entries get comment_id."""
        entry_a = {"step": "analyze", "agent": "genius", "summary": "Analysis"}
        entry_b = {"step": "plan", "agent": "genius", "summary": "Planning"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)
        comment_456 = _make_state_comment(456, history_entries=[entry_a, entry_b], version=2)

        result = extract_latest_state_from_comments([comment_123, comment_456])

        self.assertIsNotNone(result)
        self.assertEqual(result.version, 2)
        self.assertEqual(len(result.history), 2)
        self.assertEqual(result.history[0].comment_id, 123)
        self.assertEqual(result.history[1].comment_id, 456)

    def test_no_state_comments_returns_none(self):
        comments = [{"id": 999, "body": "Just a regular comment"}]
        result = extract_latest_state_from_comments(comments)
        self.assertIsNone(result)

    def test_empty_history_not_affected(self):
        """State with empty history should be returned unchanged."""
        comment = _make_state_comment(100, history_entries=[], version=1)
        result = extract_latest_state_from_comments([comment])
        self.assertIsNotNone(result)
        self.assertEqual(len(result.history), 0)

    def test_fewer_state_comments_than_history_entries(self):
        """If history has more entries than state comments, trailing stay None."""
        entry_a = {"step": "analyze", "agent": "genius"}
        entry_b = {"step": "plan", "agent": "genius"}
        entry_c = {"step": "implement", "agent": "dev"}
        # Only one state comment but history has 3 entries
        comment = _make_state_comment(123, history_entries=[entry_a, entry_b, entry_c], version=1)
        result = extract_latest_state_from_comments([comment])
        self.assertEqual(result.history[0].comment_id, 123)
        self.assertIsNone(result.history[1].comment_id)
        self.assertIsNone(result.history[2].comment_id)

    def test_preserves_existing_comment_ids(self):
        """If a history entry already has comment_id from a prior enrichment, it's preserved (re-confirmed)."""
        entry_a = {"step": "analyze", "agent": "genius", "comment_id": 789}
        entry_b = {"step": "plan", "agent": "genius", "comment_id": None}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)
        comment_456 = _make_state_comment(456, history_entries=[entry_a, entry_b], version=2)

        result = extract_latest_state_from_comments([comment_123, comment_456])

        # entry_a was originally in comment_123 (not comment_789), so it gets 123
        self.assertEqual(result.history[0].comment_id, 123)
        self.assertEqual(result.history[1].comment_id, 456)

    def test_non_state_comments_are_ignored(self):
        """Regular comments without state markers don't affect enrichment."""
        entry_a = {"step": "analyze", "agent": "genius"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)
        noise_comment = _make_comment("This is just noise", 999)
        comment_456 = _make_state_comment(456, history_entries=[entry_a], version=2)

        result = extract_latest_state_from_comments([comment_123, noise_comment, comment_456])

        self.assertEqual(result.history[0].comment_id, 123)
        # noise_comment is NOT counted as a state comment

    def test_invalid_schema_json_does_not_shift_alignment(self):
        """A comment with valid JSON but invalid WorkflowState schema must not be counted
        as a state comment, preventing index alignment shifts."""
        entry_a = {"step": "analyze", "agent": "genius"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)
        # Valid JSON inside state markers, but missing required fields → invalid WorkflowState
        invalid_json = json.dumps({"not_a_state": True})
        invalid_comment = _make_comment(
            f"{STATE_MARKER_START}\n{invalid_json}\n{STATE_MARKER_END}", 777
        )
        comment_456 = _make_state_comment(456, history_entries=[entry_a], version=2)

        result = extract_latest_state_from_comments([comment_123, invalid_comment, comment_456])

        # invalid_comment must NOT shift the alignment: history[0] → 123, not 777
        self.assertEqual(result.history[0].comment_id, 123)


if __name__ == "__main__":
    unittest.main()
