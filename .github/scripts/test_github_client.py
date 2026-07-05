"""Tests for github_client.py."""

import json
from unittest.mock import patch, MagicMock

import pytest

from github_client import (
    get_comment_body,
    get_issue_comments,
    get_comment_node_id,
    post_comment,
    minimize_comment,
)


class TestGetIssueComments:
    """Test get_issue_comments function."""

    def test_dry_run_returns_empty(self):
        result = get_issue_comments("owner/repo", 42, dry_run=True)
        assert result == []

    @patch("github_client.subprocess.run")
    def test_fetches_comments_successfully(self, mock_run):
        comments = [
            {"id": "1", "body": "Hello", "user": {"login": "user1"}},
            {"id": "2", "body": "World", "user": {"login": "user2"}},
        ]
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(comments)
        )

        result = get_issue_comments("owner/repo", 42)
        assert len(result) == 2
        assert result[0]["body"] == "Hello"
        mock_run.assert_called_once()

    @patch("github_client.subprocess.run")
    def test_handles_api_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="rate limited")

        result = get_issue_comments("owner/repo", 42)
        assert result == []

    @patch("github_client.subprocess.run")
    def test_handles_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")

        result = get_issue_comments("owner/repo", 42)
        assert result == []

    @patch("github_client.subprocess.run")
    def test_default_does_not_paginate(self, mock_run):
        """Default call (all_pages=False) must NOT include --paginate."""
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        get_issue_comments("owner/repo", 42)
        args = mock_run.call_args[0][0]
        assert "--paginate" not in args

    @patch("github_client.subprocess.run")
    def test_all_pages_includes_paginate_flag(self, mock_run):
        """When all_pages=True, --paginate must be in the gh api command."""
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        get_issue_comments("owner/repo", 42, all_pages=True)
        args = mock_run.call_args[0][0]
        assert "--paginate" in args


class TestGetCommentNodeId:
    """Test get_comment_node_id function."""

    def test_dry_run_returns_none(self):
        result = get_comment_node_id("123", "owner/repo", dry_run=True)
        assert result is None

    @patch("github_client.subprocess.run")
    def test_returns_node_id(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="C_kwPOTQ")

        result = get_comment_node_id("123", "owner/repo")
        assert result == "C_kwPOTQ"

    @patch("github_client.subprocess.run")
    def test_returns_none_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")

        result = get_comment_node_id("123", "owner/repo")
        assert result is None


class TestGetCommentBody:
    """Test get_comment_body function."""

    def test_dry_run_returns_none(self):
        result = get_comment_body("123", "owner/repo", dry_run=True)
        assert result is None

    @patch("github_client.subprocess.run")
    def test_returns_body(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="This is the comment body")

        result = get_comment_body("123", "owner/repo")
        assert result == "This is the comment body"

    @patch("github_client.subprocess.run")
    def test_returns_none_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")

        result = get_comment_body("123", "owner/repo")
        assert result is None


class TestPostComment:
    """Test post_comment function."""

    def test_dry_run(self):
        result = post_comment("owner/repo", 42, "Hello", dry_run=True)
        assert result is None

    @patch("github_client.subprocess.run")
    def test_posts_comment(self, mock_run):
        response = {"id": "999", "body": "Hello"}
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(response))

        result = post_comment("owner/repo", 42, "Hello")
        assert result["id"] == "999"


class TestMinimizeComment:
    """Test minimize_comment function."""

    def test_dry_run(self):
        assert minimize_comment("node123", dry_run=True) is True

    @patch("github_client.subprocess.run")
    def test_minimizes_successfully(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"minimizedComment":{}}')

        assert minimize_comment("node123") is True

    @patch("github_client.subprocess.run")
    def test_handles_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="fail")

        assert minimize_comment("node123") is False
