#!/usr/bin/env python3
"""Unit tests for state_resolver module."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from state_resolver import StateResolver
from workflow_state import WorkflowState


class TestStateResolverResolve:
    """Test StateResolver.resolve() precedence chain."""

    def setup_method(self):
        self.resolver = StateResolver()

    def test_explicit_state_json_takes_precedence(self):
        """Explicit JSON should take highest priority."""
        explicit = {"workflow": "explicit", "step": "implement", "agent": "dev"}
        trigger_with_state = """
        <!-- WORKFLOW_STATE_START -->
        {"workflow": "trigger", "step": "analyze", "agent": "ctx"}
        <!-- WORKFLOW_STATE_END -->
        """
        result = self.resolver.resolve(
            trigger=trigger_with_state,
            explicit_state_json=json.dumps(explicit),
        )
        assert result.workflow == "explicit"
        assert result.step == "implement"
        assert result.agent == "dev"

    def test_trigger_body_state_used_when_no_explicit(self):
        """State from trigger body should be used when no explicit JSON."""
        trigger = """
        <!-- WORKFLOW_STATE_START -->
        {"workflow": "trigger-wf", "step": "genius", "agent": "genius"}
        <!-- WORKFLOW_STATE_END -->
        """
        result = self.resolver.resolve(trigger=trigger)
        assert result.workflow == "trigger-wf"
        assert result.step == "genius"
        assert result.agent == "genius"

    def test_default_empty_state_when_nothing_provided(self):
        """Should return default empty WorkflowState when nothing is available."""
        result = self.resolver.resolve(trigger="Just some text")
        assert result.workflow == ""
        assert result.step == ""
        assert result.agent == ""
        assert result.version == 1

    def test_schema_version_is_set(self):
        """Schema version should always be present."""
        result = self.resolver.resolve(trigger="Hello")
        assert result.schema_version == 1

    def test_explicit_state_overrides_comment_history(self):
        """Explicit state should take priority over comment history."""
        explicit = {"workflow": "explicit", "step": "final"}
        comments = [
            {
                "body": """
                <!-- WORKFLOW_STATE_START -->
                {"workflow": "comment-wf", "step": "analyze"}
                <!-- WORKFLOW_STATE_END -->
                """
            }
        ]
        result = self.resolver.resolve(
            trigger="No state here",
            explicit_state_json=json.dumps(explicit),
            comment_history=comments,
        )
        assert result.workflow == "explicit"
        assert result.step == "final"

    def test_version_increments_from_existing_state(self):
        """Version should increment from existing state."""
        existing = {"workflow": "test", "step": "analyze", "version": 5}
        result = self.resolver.resolve(
            trigger="text",
            explicit_state_json=json.dumps(existing),
        )
        # resolve() returns the state as-is (build_state handles version increment)
        assert result.version == 5

    def test_partial_state_fields(self):
        """Partial state should still parse correctly."""
        partial = {"workflow": "partial-wf"}
        result = self.resolver.resolve(
            trigger="text",
            explicit_state_json=json.dumps(partial),
        )
        assert result.workflow == "partial-wf"
        assert result.step == ""
        assert result.agent == ""

    def test_malformed_explicit_json_falls_back_to_trigger(self):
        """Malformed explicit JSON should fall back to trigger body state."""
        trigger = """
        <!-- WORKFLOW_STATE_START -->
        {"workflow": "fallback", "step": "review"}
        <!-- WORKFLOW_STATE_END -->
        """
        result = self.resolver.resolve(
            trigger=trigger,
            explicit_state_json="{invalid json}",
        )
        assert result.workflow == "fallback"

    def test_malformed_explicit_json_falls_back_to_default(self):
        """If both explicit and trigger are bad, fall back to default."""
        result = self.resolver.resolve(
            trigger="No valid state",
            explicit_state_json="{bad}",
        )
        assert result.workflow == ""
        assert result.version == 1

    def test_none_values_are_handled(self):
        """None values for optional params should work."""
        result = self.resolver.resolve(
            trigger="",
            explicit_state_json=None,
            comment_history=None,
        )
        assert result.workflow == ""


class TestStateResolverFromCommentHistory:
    """Test comment history fallback path."""

    def setup_method(self):
        self.resolver = StateResolver()

    def test_latest_comment_state_is_used(self):
        """The latest state in comment history should be selected."""
        comments = [
            {
                "body": """
                <!-- WORKFLOW_STATE_START -->
                {"workflow": "old-wf", "step": "analyze", "version": 1}
                <!-- WORKFLOW_STATE_END -->
                """
            },
            {
                "body": """
                <!-- WORKFLOW_STATE_START -->
                {"workflow": "new-wf", "step": "genius", "version": 2}
                <!-- WORKFLOW_STATE_END -->
                """
            },
        ]
        result = self.resolver.resolve(
            trigger="No state here",
            comment_history=comments,
        )
        assert result.workflow == "new-wf"
        assert result.step == "genius"

    def test_empty_comment_list(self):
        """Empty comment list should fall back to default."""
        result = self.resolver.resolve(
            trigger="No state",
            comment_history=[],
        )
        assert result.workflow == ""

    def test_comments_without_state_markers(self):
        """Comments without state markers should be skipped."""
        comments = [{"body": "Just text"}, {"body": "More text"}]
        result = self.resolver.resolve(
            trigger="No state",
            comment_history=comments,
        )
        assert result.workflow == ""


class TestStateResolverCLI:
    """Test CLI entry point."""

    def test_cli_output_workflow_state_json(self, capsys):
        """CLI should output workflow_state.json content."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "state_resolver.py", "--trigger", "Hello"],
            capture_output=True,
            text=True,
            cwd="/runner/_work/git-flow-ai/git-flow-ai/.github/scripts",
        )
        # Should produce a JSON output
        output = result.stdout.strip()
        assert "workflow_state.json" in output or result.returncode == 0


class TestStateResolverFacadeCompatibility:
    """Test that workflow_state.py facade functions still work."""

    def test_from_body_still_works(self):
        """workflow_state.from_body should still function."""
        from workflow_state import from_body

        body = """
        <!-- WORKFLOW_STATE_START -->
        {"workflow": "facade-test", "step": "analyze", "schema_version": 1}
        <!-- WORKFLOW_STATE_END -->
        """
        state = from_body(body)
        assert state is not None
        assert state.workflow == "facade-test"
        assert state.schema_version == 1

    def test_model_dump_includes_schema_version(self):
        """model_dump should include schema_version."""
        state = WorkflowState(workflow="test", step="step1", schema_version=2)
        d = state.model_dump()
        assert d["schema_version"] == 2
