"""Tests for query_context.py."""

import json
import tempfile
import os

import pytest

from query_context import (
    load_event_data,
    extract_summary_from_comment,
    format_query_template,
)


class TestLoadEventData:
    """Test load_event_data function with real GitHub event JSON fixtures."""

    def _write_event(self, data):
        """Helper to write event JSON to a temp file and return path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(data, f)
        f.close()
        return f.name

    def test_full_event_returns_all_fields(self):
        """Full event JSON returns correct title, body, and author."""
        event_data = {
            "issue": {
                "title": "Fix login bug",
                "body": "The login page crashes on mobile.",
                "user": {"login": "alice"},
            }
        }
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == "Fix login bug"
        assert result["body"] == "The login page crashes on mobile."
        assert result["author"] == "alice"

        os.unlink(path)

    def test_missing_fields_default_to_empty_string(self):
        """Minimal event with missing fields defaults to empty strings."""
        event_data = {"issue": {}}
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["title"] == ""
        assert result["body"] == ""
        assert result["author"] == ""

        os.unlink(path)

    def test_missing_user_defaults_to_empty_string(self):
        """Missing user key defaults author to empty string."""
        event_data = {"issue": {"title": "Test", "body": "Body"}}
        path = self._write_event(event_data)

        result = load_event_data(path)

        assert result["author"] == ""

        os.unlink(path)


class TestExtractSummaryFromComment:
    """Test extract_summary_from_comment regex extraction."""

    def test_summary_with_marker_returns_stripped_text(self):
        """Comment with bot-agent-summary marker returns stripped summary."""
        comment = (
            "<!-- bot-agent-summary -->\n"
            "This is the summary content.\n"
            "It spans multiple lines.\n"
            "---\n"
            "rest of comment"
        )

        result = extract_summary_from_comment(comment)

        assert result == "This is the summary content.\nIt spans multiple lines."

    def test_no_marker_returns_none(self):
        """Comment without marker returns None."""
        comment = "Just a regular comment with no markers."

        result = extract_summary_from_comment(comment)

        assert result is None

    def test_whitespace_around_summary_is_stripped(self):
        """Whitespace around summary text is properly stripped."""
        comment = (
            "<!-- bot-agent-summary -->\n"
            "   \n"
            "Summary with surrounding whitespace\n"
            "   \n"
            "---\n"
            "trailing"
        )

        result = extract_summary_from_comment(comment)

        assert result == "Summary with surrounding whitespace"

    def test_flexible_spaces_in_marker(self):
        """Regex handles flexible whitespace in the HTML comment marker."""
        comment = (
            "<!--  bot-agent-summary  -->\n"
            "Flexible spaces summary\n"
            "---"
        )

        result = extract_summary_from_comment(comment)

        assert result == "Flexible spaces summary"


class TestFormatQueryTemplate:
    """Test format_query_template placeholder replacement."""

    def test_all_placeholders_replaced(self):
        """Template with all placeholders present replaces them correctly."""
        template = "Title: {title}\nBody: {body}\nBy: {author}\nHistory: {history}"

        result = format_query_template(
            title="My Title",
            body="My Body",
            author="alice",
            history="Old comment",
            template_str=template,
        )

        assert "Title: My Title" in result
        assert "Body: My Body" in result
        assert "By: alice" in result
        assert "History: Old comment" in result

    def test_partial_args_fallback_to_empty_string(self):
        """Partial or None args fall back to empty string."""
        template = "{title}{body}{author}{history}"

        result = format_query_template(
            title=None,
            body="",
            author=None,
            history="something",
            template_str=template,
        )

        assert result == "something"


class TestBuildHistoryString:
    """Test _build_history_string internal helper."""

    def test_no_comments_returns_default_message(self):
        """Empty comments list returns 'No previous comments'."""
        from query_context import _build_history_string

        result = _build_history_string([])

        assert result == "No previous comments"

    def test_comments_without_summary_use_raw_body(self):
        """Comments without summary marker use raw body text."""
        comments = [
            {"body": "Regular comment text", "user": {"login": "bob"}},
        ]

        from query_context import _build_history_string

        result = _build_history_string(comments)

        assert "### bob a écrit :" in result
        assert "Regular comment text" in result

    def test_comments_with_summary_use_extracted_summary(self):
        """Comments with summary marker use extracted summary instead of raw body."""
        comments = [
            {
                "body": "<!-- bot-agent-summary -->\nExtracted summary\n---\nfull body text here",
                "user": {"login": "charlie"},
            },
        ]

        from query_context import _build_history_string

        result = _build_history_string(comments)

        assert "### charlie a écrit :" in result
        assert "Extracted summary" in result
        assert "full body text here" not in result
