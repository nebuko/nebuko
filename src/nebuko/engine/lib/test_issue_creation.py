"""Tests for the issue creation utility (issue_creation.py)."""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from github_client import ApiCallResult, ApiErrorKind
from issue_creation import (
    GithubAPIError,
    GitHubClient,
    SplitPlan,
    TaskDefinition,
    create_sub_issues,
    load_split_plan,
    store_split_plan_state,
    trigger_workflows,
)


# ── TaskDefinition Tests ─────────────────────────────────────────────


class TestTaskDefinition:
    def test_basic(self):
        task = TaskDefinition(title="Do something", body="Details here")
        assert task.title == "Do something"
        assert task.body == "Details here"
        assert task.labels == []
        assert task.dependencies == []

    def test_with_labels(self):
        task = TaskDefinition(
            title="Fix bug", body="Fix the bug", labels=["bug", "priority"]
        )
        assert task.labels == ["bug", "priority"]

    def test_with_dependencies(self):
        task = TaskDefinition(
            title="Step 2", body="...", dependencies=[1]
        )
        assert task.dependencies == [1]

    def test_with_all_fields(self):
        task = TaskDefinition(
            title="Step 3", body="...", labels=["backend"], dependencies=[1, 2]
        )
        assert task.labels == ["backend"]
        assert task.dependencies == [1, 2]


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

    def test_dependencies_loaded(self):
        path = self._write_plan({
            "tasks": [
                {"title": "Step 1", "body": "B1", "dependencies": []},
                {"title": "Step 2", "body": "B2", "dependencies": [1]},
                {"title": "Step 3", "body": "B3", "dependencies": [1, 2]},
            ],
        })
        plan = load_split_plan(path)
        assert plan.tasks[0].dependencies == []
        assert plan.tasks[1].dependencies == [1]
        assert plan.tasks[2].dependencies == [1, 2]
        os.unlink(path)

    def test_dependencies_default_empty(self):
        path = self._write_plan({
            "tasks": [{"title": "T", "body": "B"}],
        })
        plan = load_split_plan(path)
        assert plan.tasks[0].dependencies == []
        os.unlink(path)

    def test_dependencies_not_a_list_raises(self):
        path = self._write_plan({
            "tasks": [{"title": "T", "body": "B", "dependencies": "not-a-list"}],
        })
        with pytest.raises(ValueError, match="dependencies.*must be a list"):
            load_split_plan(path)
        os.unlink(path)


def _ok_result(data) -> ApiCallResult:
    return ApiCallResult(
        ok=True, status=200, payload=json.dumps(data), error_kind=None, detail=""
    )


def _error_result(status, detail="rate limited") -> ApiCallResult:
    return ApiCallResult(
        ok=False,
        status=status,
        payload="",
        error_kind=ApiErrorKind.SECONDARY_RATE_LIMIT,
        detail=detail,
    )


class FakeTransport:
    """Records ``GitHubTransport.call`` invocations and replays scripted results."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = []

    def call(self, args, *, mutative, env=None, input_data=None):
        self.calls.append(
            {
                "args": list(args),
                "mutative": mutative,
                "input_data": input_data,
            }
        )
        return self._results.pop(0) if self._results else _ok_result({})


class TestGitHubClient:
    def test_init_uses_env_token(self):
        with patch.dict(os.environ, {"GH_TOKEN": "env-token"}):
            client = GitHubClient(repo="owner/repo")
            assert client.token == "env-token"

    def test_init_uses_explicit_token(self):
        with patch.dict(os.environ, {"GH_TOKEN": "env-token"}):
            client = GitHubClient(repo="owner/repo", token="explicit-token")
            assert client.token == "explicit-token"

    def test_create_issue(self):
        transport = FakeTransport(_ok_result({"number": 42, "id": 12345}))
        client = GitHubClient(repo="owner/repo", token="test-token", transport=transport)

        result = client.create_issue("Test Issue", "Body", ["label1"])

        assert result == {"number": 42, "id": 12345}
        call = transport.calls[0]
        assert call["args"] == [
            "api",
            "/repos/owner/repo/issues",
            "--method",
            "POST",
            "--input",
            "-",
        ]
        assert call["mutative"] is True
        assert json.loads(call["input_data"]) == {
            "title": "Test Issue",
            "body": "Body",
            "labels": ["label1"],
        }

    def test_add_sub_issue(self):
        transport = FakeTransport(_ok_result({}))
        client = GitHubClient(repo="owner/repo", token="test-token", transport=transport)

        client.add_sub_issue(parent_number=10, sub_issue_id=12345, position=1)

        call = transport.calls[0]
        assert call["args"][:2] == ["api", "/repos/owner/repo/issues/10/sub_issues"]
        assert json.loads(call["input_data"]) == {"sub_issue_id": 12345, "position": 1}

    def test_list_sub_issues(self):
        transport = FakeTransport(_ok_result([{"number": 1}, {"number": 2}]))
        client = GitHubClient(repo="owner/repo", token="test-token", transport=transport)

        subs = client.list_sub_issues(parent_number=10)

        assert len(subs) == 2
        assert subs[0]["number"] == 1
        assert transport.calls[0]["mutative"] is False

    def test_create_comment(self):
        transport = FakeTransport(_ok_result({"id": 999}))
        client = GitHubClient(repo="owner/repo", token="test-token", transport=transport)

        cid = client.create_comment(issue_number=42, body="@@architect")

        assert cid == 999

    def test_api_error_is_raised_with_status(self):
        transport = FakeTransport(_error_result(403))
        client = GitHubClient(repo="owner/repo", token="bad-token", transport=transport)

        with pytest.raises(GithubAPIError) as exc_info:
            client._request("GET", "/repos/owner/repo/issues")

        assert exc_info.value.status_code == 403


# ── create_sub_issues Integration Tests (mocked) ─────────────────────


class TestCreateSubIssues:
    @patch("issue_creation.GitHubClient")
    def test_creates_and_links_issues(self, MockClient):
        mock_client = MagicMock()
        mock_client.create_issue.side_effect = [
            {"number": 101, "id": 1001},
            {"number": 102, "id": 1002},
            {"number": 103, "id": 1003},
        ]

        plan = SplitPlan(tasks=[
            TaskDefinition(title="Task A", body="Body A"),
            TaskDefinition(title="Task B", body="Body B"),
            TaskDefinition(title="Task C", body="Body C", labels=["bug"]),
        ])

        created = create_sub_issues(mock_client, plan, parent_number=50)
        assert len(created) == 3
        assert created[0] == {"index": 1, "number": 101, "dependencies": []}
        assert created[1] == {"index": 2, "number": 102, "dependencies": []}
        assert created[2] == {"index": 3, "number": 103, "dependencies": []}
        assert mock_client.create_issue.call_count == 3
        assert mock_client.add_sub_issue.call_count == 3
        # Verify titles are prefixed with [Sub-task N/M]
        assert mock_client.create_issue.call_args_list[0][0][0] == "[Sub-task 1/3] Task A"
        assert mock_client.create_issue.call_args_list[1][0][0] == "[Sub-task 2/3] Task B"
        assert mock_client.create_issue.call_args_list[2][0][0] == "[Sub-task 3/3] Task C"
        # Verify parent label is added to all issues
        assert "parent-50" in mock_client.create_issue.call_args_list[0][0][2]
        assert "parent-50" in mock_client.create_issue.call_args_list[1][0][2]
        assert "parent-50" in mock_client.create_issue.call_args_list[2][0][2]
        # Verify existing labels are preserved alongside parent label
        assert "bug" in mock_client.create_issue.call_args_list[2][0][2]
        # Verify sub_issue_id (internal id) is passed, not display number
        assert mock_client.add_sub_issue.call_args_list[0][0][1] == 1001
        assert mock_client.add_sub_issue.call_args_list[1][0][1] == 1002
        assert mock_client.add_sub_issue.call_args_list[2][0][1] == 1003

    @patch("issue_creation.GitHubClient")
    def test_return_includes_dependencies(self, MockClient):
        mock_client = MagicMock()
        mock_client.create_issue.side_effect = [
            {"number": 101, "id": 1001},
            {"number": 102, "id": 1002},
            {"number": 103, "id": 1003},
        ]

        plan = SplitPlan(tasks=[
            TaskDefinition(title="Step 1", body="B1", dependencies=[]),
            TaskDefinition(title="Step 2", body="B2", dependencies=[1]),
            TaskDefinition(title="Step 3", body="B3", dependencies=[1, 2]),
        ])

        created = create_sub_issues(mock_client, plan, parent_number=50)
        assert created[0]["dependencies"] == []
        assert created[1]["dependencies"] == [1]
        assert created[2]["dependencies"] == [1, 2]

    @patch("issue_creation.GitHubClient")
    def test_position_ordering(self, MockClient):
        mock_client = MagicMock()
        mock_client.create_issue.return_value = {"number": 100, "id": 999}

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
        mock_client.create_issue.side_effect = [
            {"number": 101, "id": 1001},
            ValueError("API error"),
            {"number": 103, "id": 1003},
        ]

        plan = SplitPlan(tasks=[
            TaskDefinition(title="T1", body="B1"),
            TaskDefinition(title="T2", body="B2"),
            TaskDefinition(title="T3", body="B3"),
        ])

        created = create_sub_issues(mock_client, plan, parent_number=50)
        assert len(created) == 2
        assert created[0]["number"] == 101
        assert created[1]["number"] == 103
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
    def test_posts_architect_on_each_ready_task(self):
        mock_client = MagicMock()
        tasks = [
            {"index": 1, "number": 101, "dependencies": []},
            {"index": 2, "number": 102, "dependencies": []},
        ]
        trigger_workflows(mock_client, tasks)
        assert mock_client.create_comment.call_count == 2
        assert mock_client.create_comment.call_args_list[0][0][0] == 101
        assert mock_client.create_comment.call_args_list[0][0][1] == "@@architect"
        assert mock_client.create_comment.call_args_list[1][0][0] == 102

    def test_skips_tasks_with_dependencies(self):
        mock_client = MagicMock()
        tasks = [
            {"index": 1, "number": 101, "dependencies": []},
            {"index": 2, "number": 102, "dependencies": [1]},
            {"index": 3, "number": 103, "dependencies": [1, 2]},
        ]
        trigger_workflows(mock_client, tasks)
        # Only task 1 (no deps) should be triggered
        assert mock_client.create_comment.call_count == 1
        assert mock_client.create_comment.call_args_list[0][0][0] == 101

    def test_no_tasks_triggered_when_all_have_deps(self):
        mock_client = MagicMock()
        tasks = [
            {"index": 1, "number": 101, "dependencies": [2]},
            {"index": 2, "number": 102, "dependencies": [1]},
        ]
        trigger_workflows(mock_client, tasks)
        assert mock_client.create_comment.call_count == 0

    def test_trigger_error_does_not_abort_others(self, capsys):
        mock_client = MagicMock()
        # First call raises, second succeeds
        mock_client.create_comment.side_effect = [ValueError("API error"), None]
        tasks = [
            {"index": 1, "number": 101, "dependencies": []},
            {"index": 2, "number": 102, "dependencies": []},
        ]
        trigger_workflows(mock_client, tasks)
        assert mock_client.create_comment.call_count == 2


# ── store_split_plan_state Tests (mocked) ────────────────────────────


def _split_plan_state_roundtrip(body: str) -> dict:
    """Simulate the machine-parsed retrieval of split-plan-state.

    The reader searches by substring/regex (never anchored on line 1), so
    a bot marker on line 1 must not break the state round-trip.
    """
    match = re.search(r"<!-- split-plan-state: (.*?) -->", body, flags=re.DOTALL)
    assert match is not None, f"state comment not found in: {body!r}"
    return json.loads(match.group(1))


class TestStoreSplitPlanState:
    def test_posts_hidden_comment_with_state(self):
        mock_client = MagicMock()
        tasks = [
            {"index": 1, "number": 101, "dependencies": []},
            {"index": 2, "number": 102, "dependencies": [1]},
        ]
        store_split_plan_state(mock_client, parent_number=50, tasks_with_numbers=tasks)
        mock_client.create_comment.assert_called_once()
        body = mock_client.create_comment.call_args[0][1]
        assert "<!-- split-plan-state: " in body
        # Extract and parse the JSON state
        state = _split_plan_state_roundtrip(body)
        assert len(state["tasks"]) == 2
        assert state["tasks"][0]["index"] == 1
        assert state["tasks"][0]["issue_number"] == 101
        assert state["tasks"][0]["dependencies"] == []
        assert state["tasks"][1]["index"] == 2
        assert state["tasks"][1]["issue_number"] == 102
        assert state["tasks"][1]["dependencies"] == [1]
        assert state["completed"] == []

    def test_state_roundtrips_with_bot_marker_prefix(self):
        # GitHubClient.create_comment prepends the universal bot marker
        # (ensure_bot_marker) before posting. The hidden state comment must
        # still be retrievable with the marker on line 1.
        from github_client import ensure_bot_marker

        state = {
            "tasks": [
                {"index": 1, "issue_number": 101, "dependencies": []},
                {"index": 2, "issue_number": 102, "dependencies": [1]},
            ],
            "completed": [],
        }
        posted_body = ensure_bot_marker(f"<!-- split-plan-state: {json.dumps(state)} -->")
        assert posted_body.startswith("<!-- bot-workflow-terminal -->\n")
        assert _split_plan_state_roundtrip(posted_body) == state

    def test_error_is_handled_gracefully(self, capsys):
        mock_client = MagicMock()
        mock_client.create_comment.side_effect = ValueError("API error")
        tasks = [
            {"index": 1, "number": 101, "dependencies": []},
        ]
        store_split_plan_state(mock_client, parent_number=50, tasks_with_numbers=tasks)
        captured = capsys.readouterr()
        assert "FAILED to store split plan state" in captured.out


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
        mock_client.create_issue.return_value = {"number": 99, "id": 999}
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
        
        mock_client.create_issue.assert_called_once_with("[Sub-task 1/1] T", "B", ["parent-10"])
        # Verify internal id (999) is passed, not display number (99)
        mock_client.add_sub_issue.assert_called_once_with(10, 999, position=1)

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
        mock_client.get_issue_by_number.return_value = 98765
        
        with patch("sys.argv", [
            "issue_creation.py", "link",
            "--repo", "owner/repo",
            "--parent", "10",
            "--child", "42",
        ]):
            from issue_creation import main
            main()
        
        mock_client.get_issue_by_number.assert_called_once_with(42)
        # Verify internal id (98765) is passed, not display number (42)
        mock_client.add_sub_issue.assert_called_once_with(10, 98765)
