"""Tests for fetch_state feature (I/O adapter over github_client)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from nebuko.engine.lib.resolve_config.features.fetch_state.fetch_state import (  # noqa: E402
    _fetch_workflow_state_from_history,
)


def _state_block(workflow="default", step="analyze"):
    state = {"version": 1, "workflow": workflow, "step": step, "agent": "architect"}
    return (
        "<!-- WORKFLOW_STATE_START -->\n"
        + json.dumps(state)
        + "\n<!-- WORKFLOW_STATE_END -->"
    )


class TestFetchWorkflowStateFromHistory:
    def test_returns_none_without_repo(self):
        assert _fetch_workflow_state_from_history(None, 42) is None

    def test_returns_none_without_issue_number(self):
        assert _fetch_workflow_state_from_history("owner/repo", None) is None

    def test_extracts_state_from_comments(self):
        comments = [{"body": _state_block(), "id": 1}]
        with patch("resolve_config.get_issue_comments", return_value=comments):
            state = _fetch_workflow_state_from_history("owner/repo", 42)
        assert state is not None
        assert state.workflow == "default"

    def test_returns_none_on_client_error(self):
        with patch(
            "resolve_config.get_issue_comments", side_effect=RuntimeError("boom")
        ):
            assert _fetch_workflow_state_from_history("owner/repo", 42) is None