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


def _make_state_comment(comment_id: int, history_entries=None, version=1, agent=None) -> dict:
    """Create a comment containing a valid state block (optionally with a bot-agent marker)."""
    json_str = _make_state_json(history_entries, version)
    body = f"{STATE_MARKER_START}\n{json_str}\n{STATE_MARKER_END}"
    if agent:
        body = f"<!-- bot-agent-{agent} -->\n\n{body}"
    return _make_comment(body, comment_id)


def _make_agent_state_comment(comment_id: int, history_entries, version=1, agent="ctx") -> dict:
    """Create a state comment carrying the ``bot-agent-{agent}`` marker."""
    return _make_state_comment(comment_id, history_entries, version, agent=agent)


def _make_part_comment(part: int, total: int, agent: str, comment_id: int) -> dict:
    """Create a multi-part continuation comment (part 2..N): part header + agent marker, no state."""
    body = f"<!-- bot-agent-{agent} -->\n\n*(Part {part}/{total})*\n\ncontinuation content"
    return {"id": comment_id, "body": body}


class TestEnrichHistoryWithCommentIds(unittest.TestCase):
    """Test the enrichment helper function."""

    def test_enriches_matching_entries(self):
        entry_a = HistoryEntry(step="analyze", agent="genius")
        entry_b = HistoryEntry(step="plan", agent="genius")
        history = [entry_a, entry_b]
        state_comments = [{"id": 123}, {"id": 456}]

        _enrich_history_with_comment_ids(history, state_comments)

        self.assertEqual(entry_a.comment_ids, [123])
        self.assertEqual(entry_b.comment_ids, [456])

    def test_leaves_trailing_empty_when_fewer_comments(self):
        entry_a = HistoryEntry(step="analyze", agent="genius")
        entry_b = HistoryEntry(step="plan", agent="genius")
        history = [entry_a, entry_b]
        state_comments = [{"id": 123}]

        _enrich_history_with_comment_ids(history, state_comments)

        self.assertEqual(entry_a.comment_ids, [123])
        self.assertEqual(entry_b.comment_ids, [])

    def test_empty_history_is_unchanged(self):
        state_comments = [{"id": 123}]
        _enrich_history_with_comment_ids([], state_comments)  # No-op

    def test_no_state_comments_leaves_empty(self):
        entry = HistoryEntry(step="analyze", agent="genius")
        _enrich_history_with_comment_ids([entry], [])
        self.assertEqual(entry.comment_ids, [])


class TestMultiPartGrouping(unittest.TestCase):
    """Test that continuation parts (issue #504) are grouped into the owning entry."""

    def test_same_agent_parts_are_grouped(self):
        entry_ctx = HistoryEntry(step="context", agent="ctx")
        history = [entry_ctx]
        state_comment = _make_agent_state_comment(500, [{"step": "context", "agent": "ctx"}], 1, "ctx")
        part_2 = _make_part_comment(2, 2, "ctx", 501)
        part_3 = _make_part_comment(3, 3, "ctx", 502)

        all_comments = [state_comment, part_2, part_3]
        state_comments = [state_comment]
        _enrich_history_with_comment_ids(history, state_comments, all_comments)

        self.assertEqual(entry_ctx.comment_ids, [500, 501, 502])

    def test_other_agent_parts_are_not_grouped(self):
        entry_ctx = HistoryEntry(step="context", agent="ctx")
        history = [entry_ctx]
        state_comment = _make_agent_state_comment(500, [{"step": "context", "agent": "ctx"}], 1, "ctx")
        other_part = _make_part_comment(2, 2, "dev", 600)
        # A different agent's part must not leak into ctx's group
        all_comments = [state_comment, other_part]
        state_comments = [state_comment]
        _enrich_history_with_comment_ids(history, state_comments, all_comments)

        self.assertEqual(entry_ctx.comment_ids, [500])

    def test_parts_before_state_comment_are_not_grouped(self):
        entry_a = HistoryEntry(step="analyze", agent="genius")
        history = [entry_a]
        stray_part = _make_part_comment(2, 2, "genius", 400)
        state_comment = _make_agent_state_comment(500, [{"step": "analyze", "agent": "genius"}], 1, "genius")
        all_comments = [stray_part, state_comment]
        state_comments = [state_comment]
        _enrich_history_with_comment_ids(history, state_comments, all_comments)

        # Only the aligned state comment ID is recorded; the earlier stray part is dropped
        self.assertEqual(entry_a.comment_ids, [500])

    def test_part1_header_does_not_make_state_comment_a_part(self):
        # Part 1 carries both the part header and the state section; it must be
        # treated as the state comment (not skipped as a continuation part).
        entry_ctx = HistoryEntry(step="context", agent="ctx")
        history = [entry_ctx]
        json_str = _make_state_json([{"step": "context", "agent": "ctx"}], 1)
        part_1_body = (
            f"<!-- bot-agent-ctx -->\n*(Part 1/2)*\n\n"
            f"{STATE_MARKER_START}\n{json_str}\n{STATE_MARKER_END}"
        )
        state_comment = {"id": 700, "body": part_1_body}
        part_2 = _make_part_comment(2, 2, "ctx", 701)
        all_comments = [state_comment, part_2]

        result = extract_latest_state_from_comments(all_comments)
        self.assertIsNotNone(result)
        self.assertEqual(result.history[0].comment_ids, [700, 701])


class TestExtractLatestStateFromComments(unittest.TestCase):
    """Test comment_ids enrichment via extract_latest_state_from_comments."""

    def test_single_state_comment_enriches_history(self):
        """Agent B reads Agent A's comment → history[0].comment_ids is set."""
        entry_a = {"step": "analyze", "agent": "genius", "summary": "Analysis done"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)

        result = extract_latest_state_from_comments([comment_123])

        self.assertIsNotNone(result)
        self.assertEqual(len(result.history), 1)
        self.assertEqual(result.history[0].comment_ids, [123])

    def test_multiple_state_comments_enrich_all_history(self):
        """Agent C reads Agent A and B's comments → both history entries get comment_ids."""
        entry_a = {"step": "analyze", "agent": "genius", "summary": "Analysis"}
        entry_b = {"step": "plan", "agent": "genius", "summary": "Planning"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)
        comment_456 = _make_state_comment(456, history_entries=[entry_a, entry_b], version=2)

        result = extract_latest_state_from_comments([comment_123, comment_456])

        self.assertIsNotNone(result)
        self.assertEqual(result.version, 2)
        self.assertEqual(len(result.history), 2)
        self.assertEqual(result.history[0].comment_ids, [123])
        self.assertEqual(result.history[1].comment_ids, [456])

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
        """If history has more entries than state comments, trailing stay empty."""
        entry_a = {"step": "analyze", "agent": "genius"}
        entry_b = {"step": "plan", "agent": "genius"}
        entry_c = {"step": "implement", "agent": "dev"}
        # Only one state comment but history has 3 entries
        comment = _make_state_comment(123, history_entries=[entry_a, entry_b, entry_c], version=1)
        result = extract_latest_state_from_comments([comment])
        self.assertEqual(result.history[0].comment_ids, [123])
        self.assertEqual(result.history[1].comment_ids, [])
        self.assertEqual(result.history[2].comment_ids, [])

    def test_stored_comment_ids_are_preserved_during_enrichment(self):
        """Stored comment_ids values survive enrichment unchanged; only empty entries get enriched."""
        entry_a = {"step": "analyze", "agent": "genius", "comment_ids": [789]}
        entry_b = {"step": "plan", "agent": "genius", "comment_ids": []}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)
        comment_456 = _make_state_comment(456, history_entries=[entry_a, entry_b], version=2)

        result = extract_latest_state_from_comments([comment_123, comment_456])

        # entry_a's stored comment_ids (789) is preserved — not overwritten by index alignment
        self.assertEqual(result.history[0].comment_ids, [789])
        # entry_b was empty, so it gets enriched from index alignment
        self.assertEqual(result.history[1].comment_ids, [456])

    def test_legacy_scalar_comment_id_is_coerced_and_preserved(self):
        """A legacy snapshot storing scalar comment_id survives enrichment intact."""
        entry_a = {"step": "analyze", "agent": "genius", "comment_id": 789}
        entry_b = {"step": "plan", "agent": "genius"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)
        comment_456 = _make_state_comment(456, history_entries=[entry_a, entry_b], version=2)

        result = extract_latest_state_from_comments([comment_123, comment_456])

        self.assertEqual(result.history[0].comment_ids, [789])
        self.assertEqual(result.history[1].comment_ids, [456])

    def test_non_state_comments_are_ignored(self):
        """Regular comments without state markers don't affect enrichment."""
        entry_a = {"step": "analyze", "agent": "genius"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], version=1)
        noise_comment = _make_comment("This is just noise", 999)
        comment_456 = _make_state_comment(456, history_entries=[entry_a], version=2)

        result = extract_latest_state_from_comments([comment_123, noise_comment, comment_456])

        self.assertEqual(result.history[0].comment_ids, [123])
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
        self.assertEqual(result.history[0].comment_ids, [123])

    def test_multi_part_message_ids_grouped_end_to_end(self):
        """Full pipeline: a ctx step posts 3 part comments; the next agent reads them all."""
        entry_ctx = {"step": "context", "agent": "ctx", "summary": "Context report"}
        entry_genius = {"step": "analyze", "agent": "genius", "summary": "Analysis",
                        "comment_id": 123}  # legacy shape on purpose
        comment_500 = _make_agent_state_comment(500, [entry_ctx], 1, "ctx")
        part_2 = _make_part_comment(2, 3, "ctx", 501)
        part_3 = _make_part_comment(3, 3, "ctx", 502)
        comment_123 = _make_agent_state_comment(123, [entry_ctx, entry_genius], 2, "genius")

        comments = [comment_500, part_2, part_3, comment_123]
        result = extract_latest_state_from_comments(comments)

        self.assertIsNotNone(result)
        self.assertEqual(result.version, 2)
        # ctx entry picks up all three parts of its multi-part message
        self.assertEqual(result.history[0].comment_ids, [500, 501, 502])
        # genius entry keeps its stored (legacy-coerced) ID
        self.assertEqual(result.history[1].comment_ids, [123])


if __name__ == "__main__":
    unittest.main()
