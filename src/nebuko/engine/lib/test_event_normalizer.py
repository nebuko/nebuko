"""Tests for event_normalizer.py."""

import json
import tempfile
import os

import pytest

from event_normalizer import normalize_event, normalize_trigger


class TestNormalizeEvent:
    """Test normalize_event function with real GitHub event JSON fixtures."""

    def _write_event(self, data):
        """Helper to write event JSON to a temp file and return path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(data, f)
        f.close()
        return f.name

    def test_issue_comment_event(self):
        """Parse issue_comment event correctly."""
        event_data = {
            "comment": {
                "body": "@@dev please fix the bug",
                "user": {"login": "alice"},
            },
            "issue": {"number": 42},
        }
        path = self._write_event(event_data)

        result = normalize_event(path, "issue_comment", issue_number=42)

        assert result["TARGET_NUMBER"] == "42"
        assert result["TRIGGER_BODY"] == "@@dev please fix the bug"
        assert result["TRIGGER_AUTHOR"] == "alice"
        assert result["EVENT_TYPE"] == "issue_comment"

        os.unlink(path)

    def test_issues_event(self):
        """Parse issues event correctly."""
        event_data = {
            "issue": {
                "body": "Feature request description\nwith multiple lines",
                "user": {"login": "bob"},
            },
        }
        path = self._write_event(event_data)

        result = normalize_event(path, "issues", issue_number=99)

        assert result["TARGET_NUMBER"] == "99"
        assert "Feature request" in result["TRIGGER_BODY"]
        assert result["TRIGGER_AUTHOR"] == "bob"
        assert result["EVENT_TYPE"] == "issues"

        os.unlink(path)

    def test_workflow_call_event(self):
        """Parse workflow_call event correctly."""
        event_data = {"action": "called"}
        path = self._write_event(event_data)

        result = normalize_event(
            path,
            "workflow_call",
            prompt_body="Run the dev agent",
            triggering_actor="charlie",
        )

        assert result["TARGET_NUMBER"] == "0"
        assert result["TRIGGER_BODY"] == "Run the dev agent"
        assert result["TRIGGER_AUTHOR"] == "charlie"
        assert result["EVENT_TYPE"] == "workflow_call"

        os.unlink(path)

    def test_workflow_call_with_actor_fallback(self):
        """workflow_call falls back to actor when triggering_actor is empty."""
        event_data = {}
        path = self._write_event(event_data)

        result = normalize_event(path, "workflow_call", actor="dave")

        assert result["TRIGGER_AUTHOR"] == "dave"

        os.unlink(path)

    def test_empty_body_defaults_to_empty_string(self):
        """Empty body defaults to empty string, not None."""
        event_data = {
            "comment": {
                "body": "",
                "user": {"login": "empty-user"},
            },
        }
        path = self._write_event(event_data)

        result = normalize_event(path, "issue_comment")

        assert result["TRIGGER_BODY"] == ""
        assert result["TRIGGER_AUTHOR"] == "empty-user"

        os.unlink(path)

    def test_missing_user_fields_default_to_empty(self):
        """Missing user fields default to empty string."""
        event_data = {
            "comment": {"body": "no user info"},
        }
        path = self._write_event(event_data)

        result = normalize_event(path, "issue_comment")

        assert result["TRIGGER_AUTHOR"] == ""

        os.unlink(path)

    def test_multiline_body_preserved(self):
        """Multi-line trigger body is preserved intact."""
        event_data = {
            "issue": {
                "body": "Line 1\nLine 2\nLine 3",
                "user": {"login": "multiline"},
            },
        }
        path = self._write_event(event_data)

        result = normalize_event(path, "issues", issue_number=7)

        assert result["TRIGGER_BODY"] == "Line 1\nLine 2\nLine 3"

        os.unlink(path)

    def test_unknown_event_type_exits(self, capsys):
        """Unknown event type causes exit with error message."""
        event_data = {}
        path = self._write_event(event_data)

        with pytest.raises(SystemExit):
            normalize_event(path, "unknown_event")

        os.unlink(path)


class TestNormalizeTriggerIssueBody:
    """The issue body travels on the Trigger for every payload shape."""

    def test_issue_comment_carries_the_issue_body(self):
        trigger = normalize_trigger(
            {
                "comment": {"body": "/nebuko run workflow", "user": {"login": "alice"}},
                "issue": {"number": 42, "body": "the real task description"},
            }
        )

        assert trigger.issue_body == "the real task description"

    def test_issues_event_issue_body_matches_the_trigger_body(self):
        trigger = normalize_trigger(
            {"issue": {"number": 42, "body": "make it so", "user": {"login": "bob"}}}
        )

        assert trigger.issue_body == trigger.body == "make it so"

    def test_payload_without_an_issue_defaults_to_empty(self):
        trigger = normalize_trigger(
            {"inputs": {"prompt_body": "external caller"}, "sender": {"login": "ci"}}
        )

        assert trigger.issue_body == ""

    def test_null_issue_body_defaults_to_empty(self):
        trigger = normalize_trigger(
            {
                "comment": {"body": "@@dev fix", "user": {"login": "alice"}},
                "issue": {"number": 42, "body": None},
            }
        )

        assert trigger.issue_body == ""
