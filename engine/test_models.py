"""Tests for engine.models — Trigger/CommentEntry defaults and JSON round-trips."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.models import CommentEntry, Trigger, WorkflowState
from engine import scripts_dir  # noqa: E402


class TestTrigger:
    def test_field_defaults(self):
        t = Trigger()
        assert t.author == ""
        assert t.body == ""
        assert t.type == ""
        assert t.number == 0

    def test_json_round_trip(self):
        t = Trigger(author="alice", body="@@dev fix it", type="issue_comment", number=42)
        restored = Trigger.model_validate_json(t.model_dump_json())
        assert restored == t
        assert restored.number == 42


class TestCommentEntry:
    def test_field_defaults(self):
        c = CommentEntry()
        assert c.author == ""
        assert c.body == ""
        assert c.created_at == ""

    def test_json_round_trip(self):
        c = CommentEntry(
            author="bot", body="<!-- bot-workflow-terminal -->\nhello",
            created_at="2026-09-15T10:00:00Z",
        )
        restored = CommentEntry.model_validate_json(c.model_dump_json())
        assert restored == c
        assert restored.created_at == "2026-09-15T10:00:00Z"


class TestWorkflowStateReexport:
    def test_is_existing_module_model(self):
        """WorkflowState must be the existing v1 model, not a copy."""
        from workflow_state.workflow_state import WorkflowState as ExistingState

        assert WorkflowState is ExistingState

    def test_version_stays_v1(self):
        assert WorkflowState().version == 1
        # The model schema default must remain 1 (WORKFLOW_STATE v1 invariant).
        assert WorkflowState.model_fields["version"].default == 1


class TestScriptsPathHelper:
    def test_scripts_dir_resolves(self):
        d = scripts_dir()
        assert d.is_dir()
        assert (d / "event_normalizer.py").is_file()
        assert (d / "workflow_state" / "workflow_state.py").is_file()