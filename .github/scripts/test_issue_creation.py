"""Tests for the issue creation utility (issue_creation.py)."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from issue_creation import (
    GithubAPIError,
    GitHubClient,
    SplitPlan,
    TaskDefinition,
    create_sub_issues,
    load_split_plan,
    trigger_workflows,
)


# ── TaskDefinition Tests ─────────────────────────────────────────────


class TestTaskDefinition:
    def test_basic(self):
        task = TaskDefinition(title="Do something", body="Details here")
        assert task.title == "Do something"
        assert task.body == "Details here"
        assert task.labels == []

    def test_with_labels(self):
        task = TaskDefinition(
            title="Fix bug", body="Fix the bug", labels=["bug", "priority"]
        )
        assert task.labels == ["bug", "priority"]


# ── SplitPlan Tests ──────────────────────────────────────────────────


class TestSplitPlan:
    def test_default_auto_trigger(self):
        plan = SplitPlan(tasks=[TaskDefinition(title="t", body="b")])
        assert plan.auto_trigger is True

    def test_no_auto_trigger(self):
        plan = SplitPlan(
            tasks=[TaskDefinition(title="t", body="b")], auto_trigger=False
        )
        assert plan.auto_trigger is False


# ── load_split_plan Tests ────────────────────────────────────────────


class TestLoadSplitPlan:
    def _write_plan(self, data):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(data, f)
        f.close()
        return f.name

    def test_valid_plan(self):
        path = self._write_plan({
            "tasks": [
                {"title": "Task 1", "body": "Body 1"},
                {"title": "Task 2", "body": "Body 2", "labels": ["bug"]},
            ],
            "auto_trigger": True,
        })
        plan = load_split_plan(path)
        assert len(plan.tasks) == 2
        assert plan.tasks[0].title == "Task 1"
        assert plan.tasks[1].labels == ["bug"]
        assert plan.auto_trigger is True
        os.unlink(path)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_split_plan("/nonexistent/plan.json")

    def test_empty_tasks_raises(self):
        path = self._write_plan({"tasks": [], "auto_trigger": True})
        with pytest.raises(ValueError, match="non-empty"):
            load_split_plan(path)
        os.unlink(path)

    def test_invalid_schema_raises(self):
        path = self._write_plan("not a dict")
        with pytest.raises(ValueError, match="JSON object"):
            load_split_plan(path)
        os.unlink(path)

    def test_task_without_title_raises(self):
        path = self._write_plan({"tasks": [{"body": "No title"}]})
        with pytest.raises(ValueError, match="title"):
            load_split_plan(path)
        os.unlink(path)

    def test_default_auto_trigger(self):
        path = self._write_plan({"tasks": [{"title": "T", "body": "B"}]})
        plan = load_split_plan(path)
        assert plan.auto_trigger is True
        os.unlink(path)

    def test_labels_not_a_list_raises(self):
        path = self._write_plan({
            "tasks": [{"title": "T", "body": "B", "labels": "not-a-list"}],
        })
        with pytest.raises(ValueError, match="labels.*must be a list"):
            load_split_plan(path)
        os.unlink(path)


# ── GitHubClient Tests (with mocked HTTP) ───────────────────────────


def make_mock_response(data, status=200):
    """Create a mock urlopen context manager that returns JSON data."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data).encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class MockHTTPError(HTTPError):
    """HTTPError subclass with configurable attributes for testing."""

    def __init__(self, status=500, body="error"):
        self.code = status
        self._body = body
        super().__init__(
            url=None, code=status, msg=str(status), hdrs=None, fp=None
        )

    def read(self):
        return self._body.encode("utf-8")


class TestGitHubClient:
    def test_init_uses_env_token(self):
        with patch.dict(os.environ, {"GH_TOKEN": "env-token"}):
            client = GitHubClient(repo="owner/repo")
            assert client.token == "env-token"

    def test_init_uses_explicit_token(self):
        with patch.dict(os.environ, {"GH_TOKEN": "env-token"}):
            client = GitHubClient(repo="owner/repo", token="explicit-token")
            assert client.token == "explicit-token"

    @patch("issue_creation.urlopen")
    def test_create_issue(self, mock_urlopen):
        mock_urlopen.return_value = make_mock_response({"number": 42})
        client = GitHubClient(repo="owner/repo", token="test-token")
        num = client.create_issue("Test Issue", "Body", ["label1"])
        assert num == 42
        mock_urlopen.assert_called_once()

    @patch("issue_creation.urlopen")
    def test_add_sub_issue(self, mock_urlopen):
        mock_urlopen.return_value = make_mock_response({})
        client = GitHubClient(repo="owner/repo", token="test-token")
        client.add_sub_issue(parent_number=10, child_number=42, position=1)
        mock_urlopen.assert_called_once()

    @patch("issue_creation.urlopen")
    def test_list_sub_issues(self, mock_urlopen):
        mock_urlopen.return_value = make_mock_response([{"number": 1}, {"number": 2}])
        client = GitHubClient(repo="owner/repo", token="test-token")
        subs = client.list_sub_issues(parent_number=10)
        assert len(subs) == 2
        assert subs[0]["number"] == 1

    @patch("issue_creation.urlopen")
    def test_create_comment(self, mock_urlopen):
        mock_urlopen.return_value = make_mock_response({"id": 999})
        client = GitHubClient(repo="owner/repo", token="test-token")
        cid = client.create_comment(issue_number=42, body="@@genius")
        assert cid == 999

    @patch("issue_creation.urlopen")
    def test_api_error(self, mock_urlopen):
        mock_urlopen.side_effect = MockHTTPError(status=403)
        client = GitHubClient(repo="owner/repo", token="bad-token")
        with pytest.raises(GithubAPIError) as exc_info:
            client._request("GET", "/repos/owner/repo/issues")
        assert exc_info.value.status_code == 403


# ── create_sub_issues Integration Tests (mocked) ─────────────────────


class TestCreateSubIssues:
    @patch("issue_creation.GitHubClient")
    def test_creates_and_links_issues(self, MockClient):
        mock_client = MagicMock()
        mock_client.create_issue.side_effect = [101, 102, 103]

        plan = SplitPlan(tasks=[
            TaskDefinition(title="Task A", body="Body A"),
            TaskDefinition(title="Task B", body="Body B"),
            TaskDefinition(title="Task C", body="Body C", labels=["bug"]),
        ])

        created = create_sub_issues(mock_client, plan, parent_number=50)
        assert created == [101, 102, 103]
        assert mock_client.create_issue.call_count == 3
        assert mock_client.add_sub_issue.call_count == 3

    @patch("issue_creation.GitHubClient")
    def test_position_ordering(self, MockClient):
        mock_client = MagicMock()
        mock_client.create_issue.return_value = 100

        plan = SplitPlan(tasks=[
            TaskDefinition(title="T1", body="B1"),
            TaskDefinition(title="T2", body="B2"),
        ])

        create_sub_issues(mock_client, plan, parent_number=50)
        calls = mock_client.add_sub_issue.call_args_list
        assert calls[0][1]["position"] == 1
        assert calls[1][1]["position"] == 2

    @patch("issue_creation.GitHubClient")
    def test_partial_failure_continues_and_reports(self, MockClient):
        """If one task fails, remaining tasks still execute and a summary is posted."""
        mock_client = MagicMock()
        # 2nd call raises, 3rd succeeds
        mock_client.create_issue.side_effect = [101, ValueError("API error"), 103]

        plan = SplitPlan(tasks=[
            TaskDefinition(title="T1", body="B1"),
            TaskDefinition(title="T2", body="B2"),
            TaskDefinition(title="T3", body="B3"),
        ])

        created = create_sub_issues(mock_client, plan, parent_number=50)
        assert created == [101, 103]  # Only successful ones
        # Summary comment posted on parent
        mock_client.create_comment.assert_called_once()
        body = mock_client.create_comment.call_args[0][1]
        assert "Partial decomposition" in body
        assert "T2" in body
        assert "2/3" in body

    @patch("issue_creation.GitHubClient")
    def test_none_issue_number_tracked_as_failure(self, MockClient):
        """create_issue returning None is tracked as failure (no crash)."""
        mock_client = MagicMock()
        mock_client.create_issue.return_value = None

        plan = SplitPlan(tasks=[
            TaskDefinition(title="T1", body="B1"),
        ])

        created = create_sub_issues(mock_client, plan, parent_number=50)
        assert created == []  # Task failed, nothing created
        # Should have logged failure but no issues returned


# ── trigger_workflows Tests (mocked) ─────────────────────────────────


class TestTriggerWorkflows:
    @patch("issue_creation.GitHubClient")
    def test_posts_genius_on_each(self, MockClient):
        mock_client = MagicMock()
        trigger_workflows(mock_client, [101, 102])
        assert mock_client.create_comment.call_count == 2
        # create_comment(issue_number, body) uses positional args
        assert mock_client.create_comment.call_args_list[0][0][0] == 101
        assert mock_client.create_comment.call_args_list[0][0][1] == "@@genius"
        assert mock_client.create_comment.call_args_list[1][0][0] == 102


# ── CLI main() Tests ──────────────────────────────────────────────────


class TestCLIMain:
    def test_no_command_prints_help_and_exits(self, capsys):
        with patch("sys.argv", ["issue_creation.py"]):
            with pytest.raises(SystemExit) as exc_info:
                from issue_creation import main
                main()
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "usage" in captured.out.lower()

    @patch("issue_creation.load_split_plan")
    @patch("issue_creation.GitHubClient")
    def test_create_command(self, MockClient, mock_load_plan, capsys):
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.create_issue.return_value = 99
        mock_load_plan.return_value = SplitPlan(
            tasks=[TaskDefinition(title="T", body="B")], auto_trigger=False
        )
        
        with patch("sys.argv", [
            "issue_creation.py", "create",
            "--repo", "owner/repo",
            "--parent", "10",
            "--plan", "/tmp/plan.json",
            "--no-trigger",
        ]):
            from issue_creation import main
            main()
        
        mock_client.create_issue.assert_called_once_with("T", "B", [])
        mock_client.add_sub_issue.assert_called_once_with(10, 99, position=1)

    @patch("issue_creation.GitHubClient")
    def test_list_command(self, MockClient, capsys):
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.list_sub_issues.return_value = [
            {"number": 1, "title": "Sub 1"},
        ]
        
        with patch("sys.argv", [
            "issue_creation.py", "list",
            "--repo", "owner/repo",
            "--parent", "10",
        ]):
            from issue_creation import main
            main()
        
        mock_client.list_sub_issues.assert_called_once_with(10)
        captured = capsys.readouterr()
        assert "#1: Sub 1" in captured.out

    @patch("issue_creation.GitHubClient")
    def test_link_command(self, MockClient, capsys):
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        
        with patch("sys.argv", [
            "issue_creation.py", "link",
            "--repo", "owner/repo",
            "--parent", "10",
            "--child", "42",
        ]):
            from issue_creation import main
            main()
        
        mock_client.add_sub_issue.assert_called_once_with(10, 42)
