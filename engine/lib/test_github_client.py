"""Tests for github_client.py."""

import json
from unittest.mock import patch, MagicMock

import pytest

from github_client import (
    BOT_MARKER,
    get_comment_body,
    get_issue_comments,
    get_comment_node_id,
    post_comment,
    minimize_comment,
    ensure_bot_marker,
)


class TestEnsureBotMarker:
    """Test ensure_bot_marker function."""

    def test_prepends_marker_on_plain_body(self):
        result = ensure_bot_marker("Hello\nworld")
        assert result == f"{BOT_MARKER}\nHello\nworld"

    def test_marker_is_first_line(self):
        result = ensure_bot_marker("text")
        assert result.split("\n")[0] == BOT_MARKER

    def test_idempotent_when_marker_already_first_line(self):
        body = f"{BOT_MARKER}\nHello"
        assert ensure_bot_marker(body) == body

    def test_idempotent_no_duplicate(self):
        body = "text"
        assert ensure_bot_marker(ensure_bot_marker(body)) == ensure_bot_marker(body)

    def test_preserves_blank_leading_lines(self):
        result = ensure_bot_marker("\n\nHello")
        assert result.startswith(BOT_MARKER)
        assert result.count(BOT_MARKER) == 1

    def test_empty_body(self):
        result = ensure_bot_marker("")
        assert result.count(BOT_MARKER) == 1


class TestPostCommentMarker:
    """Test that post_comment tags bodies with the universal bot marker."""

    @patch("github_client.subprocess.run")
    def test_posts_comment_with_bot_marker(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"id": "1"}))

        post_comment("owner/repo", 42, "Hello")

        args = mock_run.call_args[0][0]
        body_arg = next(a for a in args if a.startswith("body="))
        assert body_arg.split("=", 1)[1].split("\n")[0] == BOT_MARKER

    @patch("github_client.subprocess.run")
    def test_does_not_duplicate_existing_marker(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"id": "1"}))

        post_comment("owner/repo", 42, f"{BOT_MARKER}\nHello")

        args = mock_run.call_args[0][0]
        body_arg = next(a for a in args if a.startswith("body="))
        assert body_arg.split("=", 1)[1].count(BOT_MARKER) == 1


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
        # ``gh api --paginate --slurp`` wraps the page(s) in an outer array.
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps([comments])
        )

        result = get_issue_comments("owner/repo", 42)
        assert len(result) == 2
        assert result[0]["body"] == "Hello"
        mock_run.assert_called_once()

    @patch("github_client.subprocess.run")
    def test_paginates_the_fetch(self, mock_run):
        """Regression (issue #750): an unpaginated fetch returns only the oldest
        page, so any ``comments[-N:]`` "last N" slice picks the wrong end."""
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps([[]]))

        result = get_issue_comments("owner/repo", 42)

        assert result == []
        args = mock_run.call_args[0][0]
        assert "--paginate" in args
        assert "--slurp" in args

    @patch("github_client.subprocess.run")
    def test_flattens_all_pages_preserving_order(self, mock_run):
        page1 = [{"id": "1", "body": "oldest"}, {"id": "2", "body": "older"}]
        page2 = [{"id": "3", "body": "newer"}, {"id": "4", "body": "newest"}]
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps([page1, page2])
        )

        result = get_issue_comments("owner/repo", 42)

        assert [c["id"] for c in result] == ["1", "2", "3", "4"]
        assert result[-1]["body"] == "newest"

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
