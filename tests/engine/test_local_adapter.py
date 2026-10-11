"""Spec-scenario tests for ``adapters/local.py`` (issue #590, plan section 3.3).

The local adapter is the driver's own harness: ``runs/<run-id>/`` mirrors a
GitHub issue thread so ``loop_guard``, ``circuit_breaker`` and state extraction
keep working unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.models import Trigger
from workflow_state.workflow_state import WorkflowState

TRIGGER_FILE = "trigger.json"
JOURNAL_FILE = "journal.md"
STATE_FILE = "state.json"
LATEST_ANSWER_FILE = "latest_answer.md"
CONSOLE_LOG_FILE = "console.log"
TERMINAL_MARKER = "<!-- bot-workflow-terminal -->"


def _trigger(number: int = 590) -> Trigger:
    return Trigger(author="olivier-norture", body="@@dev fix it", type="issue_comment", number=number)


@pytest.fixture
def run_dir(tmp_path):
    return tmp_path / "runs" / "run-1"


@pytest.fixture
def adapter(run_dir):
    return LocalAdapter(run_dir, trigger=_trigger())


class TestRunDirLayout:
    def test_init_creates_the_run_directory(self, adapter, run_dir):
        assert run_dir.is_dir()

    def test_artifact_dir_is_the_run_directory(self, adapter, run_dir):
        assert adapter.artifact_dir() == run_dir

    def test_write_artifact_writes_latest_answer(self, adapter, run_dir):
        adapter.write_artifact(LATEST_ANSWER_FILE, "the answer")

        assert (run_dir / LATEST_ANSWER_FILE).read_text() == "the answer"

    def test_write_artifact_writes_the_console_log(self, adapter, run_dir):
        adapter.write_artifact(CONSOLE_LOG_FILE, "[phase:normalize] ok\n")

        assert (run_dir / CONSOLE_LOG_FILE).read_text() == "[phase:normalize] ok\n"


class TestReadTrigger:
    def test_returns_the_injected_trigger(self, adapter):
        assert adapter.read_trigger() == _trigger()

    def test_reads_trigger_json_when_present(self, run_dir):
        payload = {"author": "olivier-norture", "body": "/nebuko run workflow", "type": "issue_comment", "number": 590}
        run_dir.mkdir(parents=True)
        (run_dir / TRIGGER_FILE).write_text(json.dumps(payload))

        assert LocalAdapter(run_dir).read_trigger() == Trigger(**payload)

    def test_trigger_json_without_issue_body_uses_the_default(self, run_dir):
        payload = {"author": "olivier-norture", "body": "/nebuko run workflow", "type": "issue_comment", "number": 590}
        run_dir.mkdir(parents=True)
        (run_dir / TRIGGER_FILE).write_text(json.dumps(payload))

        assert LocalAdapter(run_dir).read_trigger().issue_body == ""


class TestJournal:
    def test_post_appends_a_terminal_marker(self, adapter, run_dir):
        adapter.post("hello")

        assert TERMINAL_MARKER in (run_dir / JOURNAL_FILE).read_text()

    def test_post_does_not_duplicate_an_existing_marker(self, adapter, run_dir):
        adapter.post(f"{TERMINAL_MARKER}\nhello")

        assert (run_dir / JOURNAL_FILE).read_text().count(TERMINAL_MARKER) == 1

    def test_post_refreshes_the_latest_answer_artifact(self, adapter, run_dir):
        adapter.post("hello")

        assert "hello" in (run_dir / LATEST_ANSWER_FILE).read_text()

    def test_load_history_is_empty_for_a_fresh_run_dir(self, adapter):
        assert list(adapter.load_history()) == []

    def test_load_history_round_trips_posted_bodies_oldest_first(self, adapter):
        adapter.post("first")
        adapter.post("second")

        history = list(adapter.load_history())

        assert len(history) == 2
        assert history[0].body.rstrip().endswith("first")
        assert history[1].body.rstrip().endswith("second")

    def test_load_history_preserves_the_state_block_verbatim(self, adapter):
        body = (
            "<!-- workflow-continue -->\n<details>\n<summary>Workflow State</summary>\n\n"
            "```json\n<!-- WORKFLOW_STATE_START -->\n{\"version\": 1, \"workflow\": \"w\", "
            "\"step\": \"stub\", \"agent\": \"dev\"}\n<!-- WORKFLOW_STATE_END -->\n```\n</details>"
        )
        adapter.post(body)

        entry = list(adapter.load_history())[0]

        assert "<!-- WORKFLOW_STATE_START -->" in entry.body
        assert "<!-- WORKFLOW_STATE_END -->" in entry.body
        assert '"step": "stub"' in entry.body

    def test_load_history_preserves_bot_agent_markers(self, adapter):
        adapter.post(f"{TERMINAL_MARKER}\n<!-- bot-agent-dev -->\n*Dev answer*")

        assert "<!-- bot-agent-dev -->" in list(adapter.load_history())[0].body

    def test_load_history_entries_carry_iso_timestamps(self, adapter):
        adapter.post("hello")

        entry = list(adapter.load_history())[0]

        assert datetime.fromisoformat(entry.created_at.replace("Z", "+00:00")).tzinfo is not None

    def test_load_history_limit_keeps_the_most_recent_entries(self, adapter):
        adapter.post("first")
        adapter.post("second")
        adapter.post("third")

        history = list(adapter.load_history(limit=2))

        assert [entry.body.rstrip()[-6:] for entry in history] == ["second", "third"]


class TestLoadRawThread:
    """The synthetic thread shapes journal posts as bot output (issue #872)."""

    def test_fresh_run_dir_yields_no_records(self, adapter):
        assert adapter.load_raw_thread() == (None, [])

    def test_comments_carry_stable_positional_ids_and_bot_authors(self, adapter):
        adapter.post(f"{TERMINAL_MARKER}\n<!-- bot-agent-dev -->\n*Dev answer*")
        adapter.post(f"{TERMINAL_MARKER}\n<!-- bot-agent-architect -->\n*Plan*")

        issue, comments = adapter.load_raw_thread()

        assert issue is None
        assert [comment["id"] for comment in comments] == [1, 2]
        assert [comment["user"]["type"] for comment in comments] == ["Bot", "Bot"]

    def test_an_agent_answer_classifies_as_agent_output(self, adapter):
        from workflow_state.features.reconcile_thread import reconcile_thread
        from workflow_state.sub_domains.thread_record import ThreadRecordKind

        adapter.post(f"{TERMINAL_MARKER}\n<!-- bot-agent-dev -->\n*Dev answer*")

        _, comments = adapter.load_raw_thread()
        ledger = reconcile_thread(
            [], issue=None, comments=comments, trigger_user_id=0, trigger_login=""
        )

        assert ledger[0].kind is ThreadRecordKind.AGENT_OUTPUT


class TestState:
    def test_load_state_is_none_before_the_first_save(self, adapter):
        assert adapter.load_state() is None

    def test_save_then_load_round_trips_the_workflow_state(self, adapter, run_dir):
        state = WorkflowState(run_id=1, workflow="spec-driven-impl", step="stub", agent="dev")

        adapter.save_state(state)

        assert (run_dir / STATE_FILE).is_file()
        assert adapter.load_state() == state


class TestCreateIssue:
    def test_writes_a_task_file_instead_of_calling_the_api(self, adapter, run_dir):
        adapter.create_issue(title="Sub task", body="do the thing", labels=["split-plan"])

        files = sorted((run_dir / "tasks").glob("*.md"))

        assert len(files) == 1
        content = files[0].read_text()
        assert "# Sub task" in content
        assert "labels: split-plan" in content
        assert "do the thing" in content

    def test_returns_monotonic_indices(self, adapter):
        first = adapter.create_issue(title="A", body="a")
        second = adapter.create_issue(title="B", body="b")

        assert isinstance(first, int) and first > 0
        assert second == first + 1
