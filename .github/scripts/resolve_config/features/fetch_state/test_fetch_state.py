"""Tests for fetch_state feature (I/O adapter over github_client)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.features.fetch_state.fetch_state import (  # noqa: E402
    _fetch_workflow_state_from_history,
)


def _state_block(workflow="default", step="analyze"):
    state = {"version": 1, "workflow": workflow, "step": step, "agent": "genius"}
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

    def test_uses_injected_history_provider(self):
        """An injected provider is called instead of the GitHub client."""
        comments = [{"body": _state_block(workflow="wf", step="s"), "id": 1}]
        calls = []

        def provider(repo, issue_number):
            calls.append((repo, issue_number))
            return comments

        state = _fetch_workflow_state_from_history(
            "owner/repo", 42, history_provider=provider
        )
        assert state is not None
        assert state.workflow == "wf"
        assert calls == [("owner/repo", 42)]

    def test_provider_bypasses_default_client(self, mocker):
        """When a provider is given, the GitHub client must not be called."""
        spy = mocker.patch("resolve_config.get_issue_comments")
        comments = [{"body": _state_block(), "id": 1}]
        state = _fetch_workflow_state_from_history(
            "owner/repo", 42, history_provider=lambda r, n: comments
        )
        assert state is not None
        spy.assert_not_called()

    def test_provider_error_returns_none(self):
        def provider(repo, issue_number):
            raise RuntimeError("boom")

        assert (
            _fetch_workflow_state_from_history(
                "owner/repo", 42, history_provider=provider
            )
            is None
        )