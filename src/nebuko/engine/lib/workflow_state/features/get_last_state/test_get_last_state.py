import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
from workflow_state import STATE_MARKER_START, STATE_MARKER_END
from workflow_state.workflow_state import STATE_SCHEMA_VERSION
from workflow_state.features.get_last_state.get_last_state import (
    extract_latest_state_from_comments,
    _enrich_history_with_comment_ids,
    _enrich_pinned_comment_ids,
)


def _make_state_json(history_entries=None, run_id=1):
    """Build a minimal WorkflowState JSON dict."""
    return json.dumps({
        "run_id": run_id,
        "schema_version": STATE_SCHEMA_VERSION,
        "workflow": "default",
        "step": "analyze",
        "agent": "architect",
        "phase": "analysis",
        "history": history_entries or [],
    })


def _make_comment(body: str, comment_id: int) -> dict:
    """Wrap a body string in a comment dict with an id."""
    return {"id": comment_id, "body": body}


def _make_state_comment(comment_id: int, history_entries=None, run_id=1, agent=None) -> dict:
    """Create a comment containing a valid state block (optionally with a bot-agent marker)."""
    json_str = _make_state_json(history_entries, run_id)
    body = f"{STATE_MARKER_START}\n{json_str}\n{STATE_MARKER_END}"
    if agent:
        body = f"<!-- bot-agent-{agent} -->\n\n{body}"
    return _make_comment(body, comment_id)


def _make_agent_state_comment(comment_id: int, history_entries, run_id=1, agent="context") -> dict:
    """Create a state comment carrying the ``bot-agent-{agent}`` marker."""
    return _make_state_comment(comment_id, history_entries, run_id, agent=agent)


def _make_part_comment(part: int, total: int, agent: str, comment_id: int) -> dict:
    """Create a multi-part continuation comment (part 2..N): part header + agent marker, no state."""
    body = f"<!-- bot-agent-{agent} -->\n\n*(Part {part}/{total})*\n\ncontinuation content"
    return {"id": comment_id, "body": body}


class TestEnrichHistoryWithCommentIds(unittest.TestCase):
    """Test the enrichment helper function."""

    def test_enriches_matching_entries(self):
        entry_a = HistoryEntry(step="analyze", agent="architect")
        entry_b = HistoryEntry(step="plan", agent="architect")
        history = [entry_a, entry_b]
        state_comments = [{"id": 123}, {"id": 456}]

        _enrich_history_with_comment_ids(history, state_comments)

        self.assertEqual(entry_a.comment_ids, [123])
        self.assertEqual(entry_b.comment_ids, [456])

    def test_leaves_trailing_empty_when_fewer_comments(self):
        entry_a = HistoryEntry(step="analyze", agent="architect")
        entry_b = HistoryEntry(step="plan", agent="architect")
        history = [entry_a, entry_b]
        state_comments = [{"id": 123}]

        _enrich_history_with_comment_ids(history, state_comments)

        self.assertEqual(entry_a.comment_ids, [123])
        self.assertEqual(entry_b.comment_ids, [])

    def test_empty_history_is_unchanged(self):
        state_comments = [{"id": 123}]
        _enrich_history_with_comment_ids([], state_comments)  # No-op

    def test_no_state_comments_leaves_empty(self):
        entry = HistoryEntry(step="analyze", agent="architect")
        _enrich_history_with_comment_ids([entry], [])
        self.assertEqual(entry.comment_ids, [])


class TestMultiPartGrouping(unittest.TestCase):
    """Test that continuation parts (issue #504) are grouped into the owning entry."""

    def test_same_agent_parts_are_grouped(self):
        entry_context = HistoryEntry(step="context", agent="context")
        history = [entry_context]
        state_comment = _make_agent_state_comment(500, [{"step": "context", "agent": "context"}], 1, "context")
        part_2 = _make_part_comment(2, 2, "context", 501)
        part_3 = _make_part_comment(3, 3, "context", 502)

        all_comments = [state_comment, part_2, part_3]
        state_comments = [state_comment]
        _enrich_history_with_comment_ids(history, state_comments, all_comments)

        self.assertEqual(entry_context.comment_ids, [500, 501, 502])

    def test_other_agent_parts_are_not_grouped(self):
        entry_context = HistoryEntry(step="context", agent="context")
        history = [entry_context]
        state_comment = _make_agent_state_comment(500, [{"step": "context", "agent": "context"}], 1, "context")
        other_part = _make_part_comment(2, 2, "dev", 600)
        # A different agent's part must not leak into context's group
        all_comments = [state_comment, other_part]
        state_comments = [state_comment]
        _enrich_history_with_comment_ids(history, state_comments, all_comments)

        self.assertEqual(entry_context.comment_ids, [500])

    def test_parts_before_state_comment_are_not_grouped(self):
        entry_a = HistoryEntry(step="analyze", agent="architect")
        history = [entry_a]
        stray_part = _make_part_comment(2, 2, "architect", 400)
        state_comment = _make_agent_state_comment(500, [{"step": "analyze", "agent": "architect"}], 1, "architect")
        all_comments = [stray_part, state_comment]
        state_comments = [state_comment]
        _enrich_history_with_comment_ids(history, state_comments, all_comments)

        # Only the aligned state comment ID is recorded; the earlier stray part is dropped
        self.assertEqual(entry_a.comment_ids, [500])

    def test_part1_header_does_not_make_state_comment_a_part(self):
        # Part 1 carries both the part header and the state section; it must be
        # treated as the state comment (not skipped as a continuation part).
        entry_context = HistoryEntry(step="context", agent="context")
        history = [entry_context]
        json_str = _make_state_json([{"step": "context", "agent": "context"}], 1)
        part_1_body = (
            f"<!-- bot-agent-context -->\n*(Part 1/2)*\n\n"
            f"{STATE_MARKER_START}\n{json_str}\n{STATE_MARKER_END}"
        )
        state_comment = {"id": 700, "body": part_1_body}
        part_2 = _make_part_comment(2, 2, "context", 701)
        all_comments = [state_comment, part_2]

        result = extract_latest_state_from_comments(all_comments)
        self.assertIsNotNone(result)
        self.assertEqual(result.history[0].comment_ids, [700, 701])


class TestExtractLatestStateFromComments(unittest.TestCase):
    """Test comment_ids enrichment via extract_latest_state_from_comments."""

    def test_single_state_comment_enriches_history(self):
        """Agent B reads Agent A's comment → history[0].comment_ids is set."""
        entry_a = {"step": "analyze", "agent": "architect", "summary": "Analysis done"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], run_id=1)

        result = extract_latest_state_from_comments([comment_123])

        self.assertIsNotNone(result)
        self.assertEqual(len(result.history), 1)
        self.assertEqual(result.history[0].comment_ids, [123])

    def test_multiple_state_comments_enrich_all_history(self):
        """Agent C reads Agent A and B's comments → both history entries get comment_ids."""
        entry_a = {"step": "analyze", "agent": "architect", "summary": "Analysis"}
        entry_b = {"step": "plan", "agent": "architect", "summary": "Planning"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], run_id=1)
        comment_456 = _make_state_comment(456, history_entries=[entry_a, entry_b], run_id=2)

        result = extract_latest_state_from_comments([comment_123, comment_456])

        self.assertIsNotNone(result)
        self.assertEqual(result.run_id, 2)
        self.assertEqual(len(result.history), 2)
        self.assertEqual(result.history[0].comment_ids, [123])
        self.assertEqual(result.history[1].comment_ids, [456])

    def test_no_state_comments_returns_none(self):
        comments = [{"id": 999, "body": "Just a regular comment"}]
        result = extract_latest_state_from_comments(comments)
        self.assertIsNone(result)

    def test_empty_history_not_affected(self):
        """State with empty history should be returned unchanged."""
        comment = _make_state_comment(100, history_entries=[], run_id=1)
        result = extract_latest_state_from_comments([comment])
        self.assertIsNotNone(result)
        self.assertEqual(len(result.history), 0)

    def test_fewer_state_comments_than_history_entries(self):
        """If history has more entries than state comments, trailing stay empty."""
        entry_a = {"step": "analyze", "agent": "architect"}
        entry_b = {"step": "plan", "agent": "architect"}
        entry_c = {"step": "implement", "agent": "dev"}
        # Only one state comment but history has 3 entries
        comment = _make_state_comment(123, history_entries=[entry_a, entry_b, entry_c], run_id=1)
        result = extract_latest_state_from_comments([comment])
        self.assertEqual(result.history[0].comment_ids, [123])
        self.assertEqual(result.history[1].comment_ids, [])
        self.assertEqual(result.history[2].comment_ids, [])

    def test_stored_comment_ids_are_preserved_during_enrichment(self):
        """Stored comment_ids values survive enrichment unchanged; only empty entries get enriched."""
        entry_a = {"step": "analyze", "agent": "architect", "comment_ids": [789]}
        entry_b = {"step": "plan", "agent": "architect", "comment_ids": []}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], run_id=1)
        comment_456 = _make_state_comment(456, history_entries=[entry_a, entry_b], run_id=2)

        result = extract_latest_state_from_comments([comment_123, comment_456])

        # entry_a's stored comment_ids (789) is preserved — not overwritten by index alignment
        self.assertEqual(result.history[0].comment_ids, [789])
        # entry_b was empty, so it gets enriched from index alignment
        self.assertEqual(result.history[1].comment_ids, [456])

    def test_legacy_scalar_comment_id_is_coerced_and_preserved(self):
        """A legacy snapshot storing scalar comment_id survives enrichment intact."""
        entry_a = {"step": "analyze", "agent": "architect", "comment_id": 789}
        entry_b = {"step": "plan", "agent": "architect"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], run_id=1)
        comment_456 = _make_state_comment(456, history_entries=[entry_a, entry_b], run_id=2)

        result = extract_latest_state_from_comments([comment_123, comment_456])

        self.assertEqual(result.history[0].comment_ids, [789])
        self.assertEqual(result.history[1].comment_ids, [456])

    def test_non_state_comments_are_ignored(self):
        """Regular comments without state markers don't affect enrichment."""
        entry_a = {"step": "analyze", "agent": "architect"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], run_id=1)
        noise_comment = _make_comment("This is just noise", 999)
        comment_456 = _make_state_comment(456, history_entries=[entry_a], run_id=2)

        result = extract_latest_state_from_comments([comment_123, noise_comment, comment_456])

        self.assertEqual(result.history[0].comment_ids, [123])
        # noise_comment is NOT counted as a state comment

    def test_invalid_schema_json_does_not_shift_alignment(self):
        """A comment with valid JSON but invalid WorkflowState schema must not be counted
        as a state comment, preventing index alignment shifts."""
        entry_a = {"step": "analyze", "agent": "architect"}
        comment_123 = _make_state_comment(123, history_entries=[entry_a], run_id=1)
        # Valid JSON inside state markers, but missing required fields → invalid WorkflowState
        invalid_json = json.dumps({"not_a_state": True})
        invalid_comment = _make_comment(
            f"{STATE_MARKER_START}\n{invalid_json}\n{STATE_MARKER_END}", 777
        )
        comment_456 = _make_state_comment(456, history_entries=[entry_a], run_id=2)

        result = extract_latest_state_from_comments([comment_123, invalid_comment, comment_456])

        # invalid_comment must NOT shift the alignment: history[0] → 123, not 777
        self.assertEqual(result.history[0].comment_ids, [123])

    def test_multi_part_message_ids_grouped_end_to_end(self):
        """Full pipeline: a context step posts 3 part comments; the next agent reads them all."""
        entry_context = {"step": "context", "agent": "context", "summary": "Context report"}
        entry_architect = {"step": "analyze", "agent": "architect", "summary": "Analysis",
                        "comment_id": 123}  # legacy shape on purpose
        comment_500 = _make_agent_state_comment(500, [entry_context], 1, "context")
        part_2 = _make_part_comment(2, 3, "context", 501)
        part_3 = _make_part_comment(3, 3, "context", 502)
        comment_123 = _make_agent_state_comment(123, [entry_context, entry_architect], 2, "architect")

        comments = [comment_500, part_2, part_3, comment_123]
        result = extract_latest_state_from_comments(comments)

        self.assertIsNotNone(result)
        self.assertEqual(result.run_id, 2)
        # context entry picks up all three parts of its multi-part message
        self.assertEqual(result.history[0].comment_ids, [500, 501, 502])
        # architect entry keeps its stored (legacy-coerced) ID
        self.assertEqual(result.history[1].comment_ids, [123])


class TestEnrichPinnedCommentIds(unittest.TestCase):
    """Test _enrich_pinned_comment_ids() derivation of pinned_comment_ids (issue #617)."""

    def test_pinned_entry_contributes_its_comment_ids(self):
        history = [
            HistoryEntry(step="spec", agent="po", pin=True, comment_ids=[10, 11]),
            HistoryEntry(step="dev", agent="dev", pin=False, comment_ids=[12]),
        ]
        pinned: list[int] = []
        _enrich_pinned_comment_ids(history, pinned)
        self.assertEqual(pinned, [10, 11])

    def test_unpinned_entries_are_ignored(self):
        history = [HistoryEntry(step="dev", agent="dev", pin=False, comment_ids=[12])]
        pinned: list[int] = []
        _enrich_pinned_comment_ids(history, pinned)
        self.assertEqual(pinned, [])

    def test_existing_ids_preserved_and_deduplicated(self):
        history = [HistoryEntry(step="spec", agent="po", pin=True, comment_ids=[10, 11])]
        pinned = [10, 99]
        _enrich_pinned_comment_ids(history, pinned)
        self.assertEqual(pinned, [10, 99, 11])

    def test_is_idempotent(self):
        history = [HistoryEntry(step="spec", agent="po", pin=True, comment_ids=[10, 11])]
        pinned: list[int] = []
        _enrich_pinned_comment_ids(history, pinned)
        _enrich_pinned_comment_ids(history, pinned)
        self.assertEqual(pinned, [10, 11])

    def test_pinned_entry_without_comment_ids_is_noop(self):
        history = [HistoryEntry(step="spec", agent="po", pin=True)]
        pinned: list[int] = []
        _enrich_pinned_comment_ids(history, pinned)
        self.assertEqual(pinned, [])

    def test_end_to_end_pinned_entry_aligned_to_state_comment(self):
        """A pin=True entry's aligned state comment ID becomes pinned_comment_ids."""
        entry = {"step": "spec", "agent": "po", "summary": "Spec", "pin": True}
        comment = _make_state_comment(900, history_entries=[entry])
        result = extract_latest_state_from_comments([comment])
        self.assertIsNotNone(result)
        self.assertEqual(result.history[0].comment_ids, [900])
        self.assertEqual(result.pinned_comment_ids, [900])

    def test_end_to_end_unpinned_entry_not_pinned(self):
        entry = {"step": "dev", "agent": "dev", "summary": "Impl", "pin": False}
        comment = _make_state_comment(901, history_entries=[entry])
        result = extract_latest_state_from_comments([comment])
        self.assertIsNotNone(result)
        self.assertEqual(result.pinned_comment_ids, [])

    def test_end_to_end_pinned_multi_part_message(self):
        """All parts of a pinned multi-part message are pinned."""
        entry = {"step": "spec", "agent": "po", "summary": "Spec", "pin": True}
        state_comment = _make_agent_state_comment(910, [entry], 1, "po")
        part_2 = _make_part_comment(2, 2, "po", 911)
        result = extract_latest_state_from_comments([state_comment, part_2])
        self.assertIsNotNone(result)
        self.assertEqual(result.pinned_comment_ids, [910, 911])


if __name__ == "__main__":
    unittest.main()
