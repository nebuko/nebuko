"""Tests for github_client.py."""

import json
from unittest.mock import patch, MagicMock

import pytest

from nebuko.engine.lib.github_client import (
    BOT_MARKER,
    COMMENTS_PER_PAGE,
    ApiErrorKind,
    GitHubTransport,
    get_comment_body,
    get_authenticated_user,
    get_issue_comments,
    get_comment_node_id,
    post_comment,
    minimize_comment,
    ensure_bot_marker,
)


def _transport(**kwargs):
    """A transport with retries disabled and no real sleeping."""
    kwargs.setdefault("max_attempts", 1)
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return GitHubTransport(**kwargs)


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

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_posts_comment_with_bot_marker(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"id": "1"}))

        post_comment("owner/repo", 42, "Hello")

        args = mock_run.call_args[0][0]
        body_arg = next(a for a in args if a.startswith("body="))
        assert body_arg.split("=", 1)[1].split("\n")[0] == BOT_MARKER

    @patch("nebuko.engine.lib.github_client.subprocess.run")
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

    @patch("nebuko.engine.lib.github_client.subprocess.run")
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

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_paginates_the_fetch(self, mock_run):
        """Regression (issue #750): an unpaginated fetch returns only the oldest
        page, so any ``comments[-N:]`` "last N" slice picks the wrong end."""
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps([[]]))

        result = get_issue_comments("owner/repo", 42)

        assert result == []
        args = mock_run.call_args[0][0]
        assert "--paginate" in args
        assert "--slurp" in args

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_flattens_all_pages_preserving_order(self, mock_run):
        page1 = [{"id": "1", "body": "oldest"}, {"id": "2", "body": "older"}]
        page2 = [{"id": "3", "body": "newer"}, {"id": "4", "body": "newest"}]
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps([page1, page2])
        )

        result = get_issue_comments("owner/repo", 42)

        assert [c["id"] for c in result] == ["1", "2", "3", "4"]
        assert result[-1]["body"] == "newest"

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_handles_api_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="rate limited")

        result = get_issue_comments("owner/repo", 42, transport=_transport())
        assert result == []

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_handles_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")

        result = get_issue_comments("owner/repo", 42)
        assert result == []

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_limit_keeps_the_last_comments(self, mock_run):
        page = [{"id": str(index), "body": f"c{index}"} for index in range(5)]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps([page]))

        result = get_issue_comments("owner/repo", 42, limit=2)

        assert [c["body"] for c in result] == ["c3", "c4"]

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_tail_fetch_falls_back_when_link_header_is_missing(self, mock_run):
        """A multi-page thread whose ``Link`` header is absent must not be
        mistaken for a single page of the *newest* comments (issue #805)."""
        first_page = [
            {"id": str(i), "body": f"old{i}"} for i in range(COMMENTS_PER_PAGE)
        ]
        newest = [{"id": "n1", "body": "newer"}, {"id": "n2", "body": "newest"}]
        # First a full first page with no Link header, then the paginated read.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(first_page)),
            MagicMock(returncode=0, stdout=json.dumps([first_page, newest])),
        ]

        result = get_issue_comments("owner/repo", 42, limit=2)

        assert [c["body"] for c in result] == ["newer", "newest"]
        assert mock_run.call_count == 2
        assert "--paginate" in mock_run.call_args_list[1][0][0]


class TestGetAuthenticatedUser:
    """``GET /user`` self-identity anchors ledger bot-ness (issue #872 review)."""

    def test_dry_run_returns_none(self):
        assert get_authenticated_user(dry_run=True) is None

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_returns_id_login_and_type(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"id": 42, "login": "nebuko-engine", "type": "User"}),
        )

        result = get_authenticated_user(transport=_transport())

        assert result == {"id": 42, "login": "nebuko-engine", "type": "User"}
        args = mock_run.call_args[0][0]
        assert args[:2] == ["gh", "api"]
        assert "/user" in args

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_api_error_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="bad creds")

        assert get_authenticated_user(transport=_transport()) is None

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_invalid_json_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")

        assert get_authenticated_user(transport=_transport()) is None


class TestGetCommentNodeId:
    """Test get_comment_node_id function."""

    def test_dry_run_returns_none(self):
        result = get_comment_node_id("123", "owner/repo", dry_run=True)
        assert result is None

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_returns_node_id(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="C_kwPOTQ")

        result = get_comment_node_id("123", "owner/repo")
        assert result == "C_kwPOTQ"

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_returns_none_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")

        result = get_comment_node_id("123", "owner/repo", transport=_transport())
        assert result is None


class TestGetCommentBody:
    """Test get_comment_body function."""

    def test_dry_run_returns_none(self):
        result = get_comment_body("123", "owner/repo", dry_run=True)
        assert result is None

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_returns_body(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="This is the comment body")

        result = get_comment_body("123", "owner/repo")
        assert result == "This is the comment body"

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_returns_none_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")

        result = get_comment_body("123", "owner/repo", transport=_transport())
        assert result is None


class TestPostComment:
    """Test post_comment function."""

    def test_dry_run(self):
        result = post_comment("owner/repo", 42, "Hello", dry_run=True)
        assert result.ok is True

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_posts_comment(self, mock_run):
        response = {"id": 999, "html_url": "https://example.test/c/999"}
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(response))

        result = post_comment("owner/repo", 42, "Hello")

        assert result.ok is True
        assert result.comment_id == 999
        assert result.comment_url == "https://example.test/c/999"

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_failure_is_reported_not_swallowed(self, mock_run):
        """The regression of issue #805: a dropped write must be visible."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr='gh: You have exceeded a secondary rate limit (HTTP 403)',
        )

        result = post_comment("owner/repo", 42, "Hello", transport=_transport())

        assert result.ok is False
        assert result.error_kind == ApiErrorKind.SECONDARY_RATE_LIMIT


class TestMinimizeComment:
    """Test minimize_comment function."""

    def test_dry_run(self):
        assert minimize_comment("node123", dry_run=True) is True

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_minimizes_successfully(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"minimizedComment":{}}')

        assert minimize_comment("node123") is True

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_handles_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fail")

        assert minimize_comment("node123", transport=_transport()) is False


class TestGitHubTransport:
    """The rate-limit-aware choke point (issue #805)."""

    def _ok(self, stdout="{}", headers=""):
        head = "HTTP/2.0 200 OK" + (f"\n{headers}" if headers else "")
        return MagicMock(returncode=0, stdout=f"{head}\n\n{stdout}", stderr="")

    def _error(self, status, message, headers=""):
        return MagicMock(
            returncode=1,
            stdout="",
            stderr=f"gh: {message} (HTTP {status})",
        )

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_success_parses_status_and_body(self, mock_run):
        mock_run.return_value = self._ok(stdout='{"id": 7}')

        result = _transport().call(["api", "/repos/o/r/issues/1"], mutative=False)

        assert result.ok is True
        assert result.status == 200
        assert result.payload == '{"id": 7}'

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_classifies_a_secondary_rate_limit(self, mock_run):
        mock_run.return_value = self._error(403, "You have exceeded a secondary rate limit")

        result = _transport().call(["api", "/x"], mutative=True)

        assert result.error_kind == ApiErrorKind.SECONDARY_RATE_LIMIT

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_classifies_a_primary_rate_limit_from_zero_remaining(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="HTTP/2.0 403 Forbidden\nx-ratelimit-remaining: 0\n\n{}",
            stderr="gh: API rate limit exceeded (HTTP 403)",
        )

        result = _transport().call(["api", "/x"], mutative=False)

        assert result.error_kind == ApiErrorKind.PRIMARY_RATE_LIMIT

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_classifies_auth_and_not_found_as_terminal(self, mock_run):
        for status, message, expected in (
            (401, "Bad credentials", ApiErrorKind.AUTH),
            (404, "Not Found", ApiErrorKind.NOT_FOUND),
        ):
            mock_run.return_value = self._error(status, message)
            result = _transport().call(["api", "/x"], mutative=False)
            assert result.error_kind == expected

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_classifies_an_empty_body_decode_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="unexpected end of JSON input",
        )

        result = _transport().call(["api", "/x"], mutative=False)

        assert result.error_kind == ApiErrorKind.DECODE

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_retries_a_secondary_block_then_succeeds(self, mock_run):
        sleeps = []
        mock_run.side_effect = [
            self._error(403, "You have exceeded a secondary rate limit"),
            self._ok(stdout="{}"),
        ]
        transport = GitHubTransport(
            max_attempts=3,
            sleep=sleeps.append,
            jitter=lambda: 0.0,
            secondary_cooldown_s=5.0,
        )

        result = transport.call(["api", "/x"], mutative=True)

        assert result.ok is True
        assert transport.retries == 1
        assert sleeps and sleeps[0] >= 5.0

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_does_not_retry_a_terminal_failure(self, mock_run):
        mock_run.return_value = self._error(401, "Bad credentials")

        transport = _transport(max_attempts=4)
        result = transport.call(["api", "/x"], mutative=False)

        assert result.error_kind == ApiErrorKind.AUTH
        assert mock_run.call_count == 1

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_honours_retry_after_before_retrying(self, mock_run):
        sleeps = []
        mock_run.side_effect = [
            MagicMock(
                returncode=1,
                stdout="HTTP/2.0 403 Forbidden\nretry-after: 7\n\n{}",
                stderr="gh: secondary rate limit (HTTP 403)",
            ),
            self._ok(stdout="{}"),
        ]
        transport = GitHubTransport(
            max_attempts=2, sleep=sleeps.append, jitter=lambda: 0.0
        )

        transport.call(["api", "/x"], mutative=True)

        # The server-mandated Retry-After is honoured, not the 60s default.
        assert sleeps and sleeps[0] == pytest.approx(7.0, abs=0.2)

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_paces_mutative_requests(self, mock_run):
        clock = {"now": 0.0}
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        mock_run.return_value = self._ok(stdout="{}")
        transport = GitHubTransport(
            max_attempts=1,
            min_write_gap_s=1.0,
            sleep=sleep,
            monotonic=lambda: clock["now"],
            jitter=lambda: 0.0,
        )

        transport.call(["api", "/a"], mutative=True)
        transport.call(["api", "/b"], mutative=True)

        assert any(seconds >= 1.0 for seconds in sleeps)

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_reads_do_not_add_headers_for_paginated_calls(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")

        _transport().call(["api", "--paginate", "--slurp", "/x"], mutative=False)

        args = mock_run.call_args[0][0]
        assert "--include" not in args

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_forwards_cwd_and_timeout_to_subprocess(self, mock_run):
        """``ensure_pr`` runs git/gh from the real checkout, so the transport
        must pass ``cwd``/``timeout`` through to ``subprocess.run``."""
        mock_run.return_value = self._ok(stdout="{}")

        _transport().call(
            ["api", "/x"], mutative=False, cwd="/real/checkout", timeout=30
        )

        kwargs = mock_run.call_args.kwargs
        assert kwargs["cwd"] == "/real/checkout"
        assert kwargs["timeout"] == 30
