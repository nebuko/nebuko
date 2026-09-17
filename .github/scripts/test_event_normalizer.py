"""Tests for event_normalizer.py."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Repo root must be importable for `engine.models` regardless of pytest CWD.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from event_normalizer import normalize_event


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


class TestNormalizeTrigger:
    """Test the pure normalize_trigger(payload) -> Trigger seam (issue #590)."""

    _ISSUE_COMMENT_PAYLOAD = {
        "comment": {
            "body": "@@dev please fix it",
            "user": {"login": "olivier-norture"},
        }
    }

    def test_trigger_fields_match_normalize_event(self):
        """Trigger fields equal the KEY=value values normalize_event emits."""
        from event_normalizer import normalize_event, normalize_trigger

        data = self._ISSUE_COMMENT_PAYLOAD
        path = self._write_event(data)
        try:
            kv = normalize_event(path, "issue_comment", issue_number=90)
            trigger = normalize_trigger(data, "issue_comment", issue_number=90)
        finally:
            os.unlink(path)

        assert trigger.author == kv["TRIGGER_AUTHOR"]
        assert trigger.body == kv["TRIGGER_BODY"]
        assert trigger.type == kv["EVENT_TYPE"]
        assert str(trigger.number) == kv["TARGET_NUMBER"]

    def test_trigger_is_pydantic_model(self):
        from engine.models import Trigger
        from event_normalizer import normalize_trigger

        trigger = normalize_trigger(
            self._ISSUE_COMMENT_PAYLOAD, "issue_comment", issue_number=90
        )
        assert isinstance(trigger, Trigger)
        assert trigger.number == 90

    def test_workflow_call_trigger_fields(self):
        from event_normalizer import normalize_trigger

        payload = {"workflow_run": {"triggering_actor": {"login": "ci-bot"}}}
        trigger = normalize_trigger(
            payload,
            "workflow_call",
            prompt_body="do the thing",
            triggering_actor="ci-bot",
            actor="fallback",
        )
        assert trigger.type == "workflow_call"
        assert trigger.author == "ci-bot"
        assert trigger.body == "do the thing"
        assert trigger.number == 0

    def test_unknown_event_exits(self):
        from event_normalizer import normalize_trigger

        with pytest.raises(SystemExit):
            normalize_trigger({}, "unknown_event")

    def _write_event(self, data):
        import json
        import tempfile

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(data, f)
        f.close()
        return f.name
