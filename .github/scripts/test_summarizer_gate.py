"""Tests for summarizer_gate.py."""

import pytest

from summarizer_gate import (
    has_summary_comment,
    count_non_bot_comments,
)


class TestHasSummaryComment:
    """Test summary comment detection."""

    def test_no_comments(self):
        assert has_summary_comment([]) is False

    def test_normal_comments_only(self):
        comments = [{"body": "Regular comment"}]
        assert has_summary_comment(comments) is False

    def test_summary_marker_present(self):
        comments = [{"body": "<!-- bot-agent-summary -->\nHere's a summary..."}]
        assert has_summary_comment(comments) is True

    def test_other_bot_markers_ignored(self):
        comments = [
            {"body": "<!-- bot-agent-dev -->\nDev response"},
            {"body": "<!-- bot-agent-genius -->\nGenius plan"},
        ]
        assert has_summary_comment(comments) is False

    def test_summary_among_other_comments(self):
        comments = [
            {"body": "Human comment"},
            {"body": "<!-- bot-agent-dev -->\nResponse"},
            {"body": "<!-- bot-agent-summary -->\nFull summary"},
        ]
        assert has_summary_comment(comments) is True

    def test_empty_body_does_not_crash(self):
        comments = [{"body": ""}]
        assert has_summary_comment(comments) is False


class TestCountNonBotComments:
    """Test non-bot comment counting."""

    def test_no_comments(self):
        assert count_non_bot_comments([]) == 0

    def test_all_human_comments(self):
        comments = [
            {"body": "First human comment"},
            {"body": "Second human comment"},
            {"body": "Third human comment"},
        ]
        assert count_non_bot_comments(comments) == 3

    def test_all_bot_comments(self):
        comments = [
            {"body": "<!-- bot-agent-dev -->\nResponse"},
            {"body": "<!-- bot-agent-ctx -->\nContext"},
        ]
        assert count_non_bot_comments(comments) == 0

    def test_mixed_comments(self):
        comments = [
            {"body": "Human says hi"},
            {"body": "<!-- bot-agent-dev -->\nBot response"},
            {"body": "Another human comment"},
            {"body": "<!-- bot-agent-summary -->\nSummary"},
        ]
        assert count_non_bot_comments(comments) == 2

    def test_empty_body_counts_as_non_bot(self):
        comments = [{"body": ""}, {"body": "<!-- bot-agent-dev -->"}]
        assert count_non_bot_comments(comments) == 1

    def test_bot_marker_in_middle_of_text(self):
        body = "Some text <!-- bot-agent-dev --> more text"
        comments = [{"body": body}]
        assert count_non_bot_comments(comments) == 0

    def test_universal_marker_counts_as_bot(self):
        comments = [
            {"body": "<!-- bot-workflow-terminal -->\nResponse"},
            {"body": "Human comment"},
        ]
        assert count_non_bot_comments(comments) == 1
