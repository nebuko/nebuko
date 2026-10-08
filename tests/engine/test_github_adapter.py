"""Spec-scenario tests for ``adapters/github.py`` (issue #590, plan section 3.4).

The GitHub adapter composes the *existing* ``gh``/REST clients, so the client
surface is injected as a module-shaped stand-in in tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nebuko.adapters.github import GitHubAdapter
from nebuko.engine.models import Trigger

ISSUE_COMMENT_PAYLOAD = {
    "action": "created",
    "comment": {
        "id": 5927119915,
        "body": "/nebuko run workflow --name spec-driven-impl",
        "created_at": "2026-10-01T07:47:47Z",
        "user": {"login": "olivier-norture"},
    },
    "issue": {
        "number": 590,
        "title": "[engine] Stage A",
        "body": "Carry the issue body into the workflow-run context.",
        "user": {"login": "olivier-norture"},
    },
    "repository": {"full_name": "olivier-norture/git-flow-ai"},
}


class FakeGhClient:
    """Stand-in for the ``github_client`` module (function surface only)."""

    def __init__(self, comments=(), posted=None):
        self.comments = list(comments)
        self.posted = posted if posted is not None else {"id": 4242, "html_url": "https://example.test/c/4242"}
        self.calls = []
        self.deleted = []
        self.limits = []

    def get_issue_comments(self, repo, issue_number, token=None, dry_run=False, limit=None):
        self.calls.append(("get_issue_comments", repo, issue_number))
        self.limits.append(limit)
        comments = list(self.comments)
        if limit is not None:
            comments = comments[-limit:] if limit > 0 else []
        return comments

    def post_comment(self, repo, issue_number, body, token=None, dry_run=False):
        self.calls.append(("post_comment", repo, issue_number, body))
        return self.posted

    def delete_comment(self, repo, comment_id, token=None, dry_run=False):
        self.calls.append(("delete_comment", repo, comment_id))
        self.deleted.append(comment_id)
        return True


def _event_file(tmp_path, payload):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload))
    return path


def _adapter(tmp_path, client):
    """GitHub adapter wired to the recorded issue-comment event."""
    return GitHubAdapter(
        repo="olivier-norture/git-flow-ai",
        event_path=_event_file(tmp_path, ISSUE_COMMENT_PAYLOAD),
        client=client,
    )


class TestReadTrigger:
    def test_normalizes_a_recorded_issue_comment_event(self, tmp_path):
        adapter = GitHubAdapter(
            repo="olivier-norture/git-flow-ai",
            event_path=_event_file(tmp_path, ISSUE_COMMENT_PAYLOAD),
            client=FakeGhClient(),
        )

        trigger = adapter.read_trigger()

        assert isinstance(trigger, Trigger)
        assert (trigger.author, trigger.body, trigger.type, trigger.number) == (
            "olivier-norture",
            "/nebuko run workflow --name spec-driven-impl",
            "issue_comment",
            590,
        )
        assert trigger.issue_body == ISSUE_COMMENT_PAYLOAD["issue"]["body"]

    def test_defaults_to_the_github_event_path_environment_variable(self, tmp_path, monkeypatch):
        path = _event_file(tmp_path, ISSUE_COMMENT_PAYLOAD)
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
        adapter = GitHubAdapter(repo="olivier-norture/git-flow-ai", client=FakeGhClient())

        assert adapter.read_trigger().number == 590


class TestHistory:
    def test_maps_client_comments_to_comment_entries(self, tmp_path):
        client = FakeGhClient(
            comments=[
                {
                    "body": "<!-- bot-workflow-terminal -->\nfirst",
                    "user": {"login": "olivier-norture"},
                    "created_at": "2026-10-01T07:47:47Z",
                }
            ]
        )
        adapter = _adapter(tmp_path, client)
        adapter.read_trigger()

        history = list(adapter.load_history())

        assert len(history) == 1
        assert history[0].author == "olivier-norture"
        assert history[0].body.endswith("first")
        assert history[0].created_at == "2026-10-01T07:47:47Z"

    def test_reads_the_issue_number_from_the_trigger(self, tmp_path):
        client = FakeGhClient()
        adapter = _adapter(tmp_path, client)

        adapter.read_trigger()
        list(adapter.load_history())

        assert ("get_issue_comments", "olivier-norture/git-flow-ai", 590) in client.calls


class TestPost:
    def test_returns_the_posted_comment_reference(self, tmp_path):
        client = FakeGhClient()
        adapter = _adapter(tmp_path, client)
        adapter.read_trigger()

        result = adapter.post("hello")

        assert "4242" in result
        assert ("post_comment", "olivier-norture/git-flow-ai", 590, "hello") in client.calls


class TestDeleteIssueComments:
    """Batch comment deletion through the composed REST client (issue #724)."""

    def test_deletes_every_comment_by_default(self, tmp_path):
        client = FakeGhClient(comments=[{"id": 10}, {"id": 20}, {"id": 30}])
        adapter = _adapter(tmp_path, client)

        deleted = adapter.delete_issue_comments("590")

        assert deleted == 3
        assert client.deleted == [10, 20, 30]
        assert ("get_issue_comments", "olivier-norture/git-flow-ai", 590) in client.calls

    def test_from_boundary_deletes_that_comment_and_every_later_one(self, tmp_path):
        client = FakeGhClient(comments=[{"id": 10}, {"id": 20}, {"id": 30}])
        adapter = _adapter(tmp_path, client)

        deleted = adapter.delete_issue_comments("590", from_comment="20")

        assert deleted == 2
        assert client.deleted == [20, 30]

    def test_skips_comments_without_an_id(self, tmp_path):
        client = FakeGhClient(comments=[{"body": "no id"}, {"id": 20}])
        adapter = _adapter(tmp_path, client)

        assert adapter.delete_issue_comments("590") == 1
        assert client.deleted == [20]


class TestThreadSnapshotReuse:
    """One bounded thread read per run (issue #805, Pillar C).

    ``load_history`` and ``load_state`` used to list the whole comment thread
    independently; they now share a single snapshot, and an explicit history
    bound is floored so a state lookup can never be truncated away.
    """

    def test_history_and_state_share_a_single_fetch(self, tmp_path):
        _, body = _state_comment()
        client = FakeGhClient(comments=[{"body": body, "user": {"login": "x"}}])
        adapter = _adapter(tmp_path, client)
        adapter.read_trigger()

        history = adapter.load_history(limit=5)
        state = adapter.load_state()

        assert len(history) == 1
        assert state is not None
        assert [call for call in client.calls if call[0] == "get_issue_comments"] == [
            ("get_issue_comments", "olivier-norture/git-flow-ai", 590)
        ]

    def test_a_low_history_limit_still_reserves_a_state_safe_tail(self, tmp_path):
        from thread_snapshot import STATE_TAIL_FLOOR

        client = FakeGhClient(comments=[{"body": "hi", "user": {"login": "x"}}])
        adapter = _adapter(tmp_path, client)
        adapter.read_trigger()

        adapter.load_history(limit=1)

        assert client.limits == [STATE_TAIL_FLOOR]

    def test_a_failed_fetch_is_not_read_as_an_empty_thread(self, tmp_path):
        from github_client import ApiErrorKind, CommentsFetchResult

        class FailingClient:
            def fetch_issue_comments(self, repo, number, token=None, limit=None):
                return CommentsFetchResult(
                    ok=False, error_kind=ApiErrorKind.SECONDARY_RATE_LIMIT, detail="403"
                )

        adapter = _adapter(tmp_path, FailingClient())
        adapter.read_trigger()

        assert list(adapter.load_history(limit=5)) == []
        assert adapter.load_state() is None
        assert adapter._thread_snapshot().fetch_error == "403"


class TestArtifacts:
    """Run logs are staged under ``$RUNNER_TEMP`` for the CI artifact (issue #743)."""

    def test_artifact_dir_is_the_named_staging_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        adapter = _adapter(tmp_path, FakeGhClient())

        assert adapter.artifact_dir() == tmp_path / "nebuko-task-logs"

    def test_write_artifact_creates_the_staging_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        adapter = _adapter(tmp_path, FakeGhClient())

        adapter.write_artifact("console.log", "log line\n")

        staged = tmp_path / "nebuko-task-logs" / "console.log"
        assert staged.read_text() == "log line\n"


class TestImportBootstrap:
    """The adapter bootstraps ``engine/lib`` itself (round-4, finding 1).

    ``tests/conftest.py`` seeds ``sys.path`` for the whole suite, which masked a
    real bug: under the production ``python -m nebuko.engine.run`` bootstrap only the
    repository root is importable, so ``from event_normalizer import ...``
    raised ``ModuleNotFoundError``. This exercises the adapter out-of-process
    with only the repository root on ``PYTHONPATH``, i.e. no conftest seeding.
    """

    def test_read_trigger_bootstraps_the_legacy_path_without_conftest(self, tmp_path):
        repo_root = Path(__file__).resolve().parents[2]
        event_path = _event_file(tmp_path, ISSUE_COMMENT_PAYLOAD)
        program = (
            "from nebuko.adapters.github import GitHubAdapter\n"
            "adapter = GitHubAdapter(\n"
            "    repo='olivier-norture/git-flow-ai',\n"
            f"    event_path={str(event_path)!r},\n"
            ")\n"
            "trigger = adapter.read_trigger()\n"
            "print(trigger.number, trigger.type)\n"
        )
        env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
        env.pop("GITHUB_EVENT_PATH", None)

        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "590 issue_comment"

    def test_state_methods_bootstrap_the_legacy_path_without_conftest(self, tmp_path):
        """``load_state``/``save_state`` import legacy packages too (round-4, finding 1)."""
        repo_root = Path(__file__).resolve().parents[2]
        event_path = _event_file(tmp_path, ISSUE_COMMENT_PAYLOAD)
        program = (
            "from nebuko.adapters.github import GitHubAdapter\n"
            "class Client:\n"
            "    def get_issue_comments(self, repo, number, limit=None):\n"
            "        return []\n"
            f"adapter = GitHubAdapter(repo='x/y', event_path={str(event_path)!r}, client=Client())\n"
            # Legacy imports only resolve after the adapter's bootstrap, exactly
            # as in the production ``python -m nebuko.engine.run`` ordering.
            "from workflow_state.workflow_state import WorkflowState\n"
            "adapter.read_trigger()\n"
            "print(adapter.load_state())\n"
            "adapter.save_state(WorkflowState(version=1, workflow='spec-driven-impl', "
            "step='stub', agent='dev'))\n"
        )
        env = {**os.environ, "PYTHONPATH": str(repo_root / "src"), "RUNNER_TEMP": str(tmp_path)}
        env.pop("GITHUB_EVENT_PATH", None)

        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "None"
        assert (tmp_path / "prev_workflow_state.json").is_file()


class TestSaveState:
    """``save_state`` is the hand-off write only (round-4, finding 6).

    ``Engine.persist`` re-saves the *loaded* previous state when the agent
    produced no artefact, so ``save_state`` must be side-effect free beyond the
    hand-off file: ``WorkflowStateCodec.save_prev`` appends no history and posts
    nothing, which is what makes that fallback safe.
    """

    def test_writes_only_the_handoff_file_and_posts_nothing(self, tmp_path, monkeypatch):
        from workflow_state.workflow_state import WorkflowState

        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        client = FakeGhClient()
        adapter = _adapter(tmp_path, client)

        adapter.save_state(
            WorkflowState(version=1, workflow="spec-driven-impl", step="stub", agent="dev")
        )

        assert (tmp_path / "prev_workflow_state.json").is_file()
        assert client.calls == []


def _state_comment(*, history_len: int = 2):
    """A posted bot comment carrying a state block with ``history_len`` entries."""
    from config import StepRef
    from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
    from workflow_state.workflow_state import (
        STATE_MARKER_END,
        STATE_MARKER_START,
        WorkflowState,
    )

    entries = [
        HistoryEntry(step=f"step_{index}", agent="genius", summary=f"s{index}")
        for index in range(history_len)
    ]
    state = WorkflowState(
        version=7,
        workflow="spec-driven-impl",
        step="plan",
        agent="genius",
        position=[StepRef(workflow="spec-driven-impl", step="plan")],
        history=entries,
    )
    body = (
        "<!-- bot-workflow-terminal -->\n<!-- bot-agent-genius -->\n"
        f"{STATE_MARKER_START}\n{state.model_dump_json()}\n{STATE_MARKER_END}"
    )
    return state, body


class TestStagePrevState:
    """``Engine.stage_prev_state`` seeds the pre-agent hand-off (issue #739).

    The refactor left the engine's only state write in ``persist`` — *after* the
    agent — so the child read an absent (fresh runner) or stale (persistent
    runner) ``$RUNNER_TEMP`` hand-off and ``build_state`` reset ``history`` to a
    single entry (issues #726/#739). The engine now writes the latest posted
    state through the same codec the child reads it back with.
    """

    def test_stages_the_latest_posted_state_into_the_handoff(self, tmp_path, monkeypatch):
        from nebuko.engine.paths import repo_root
        from nebuko.engine.run_loop import Engine
        from workflow_state.state_codec import WorkflowStateCodec

        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        previous, body = _state_comment(history_len=3)
        comments = [
            {"body": "<!-- bot-workflow-terminal -->\nolder", "user": {"login": "x"}},
            {"body": body, "user": {"login": "x"}},
        ]
        adapter = _adapter(tmp_path, FakeGhClient(comments=comments))
        engine = Engine(adapter=adapter, repo_root=repo_root(), env={})

        staged = engine.stage_prev_state()

        assert staged is not None
        load = WorkflowStateCodec().load_prev()
        assert load.status == "ok"
        assert load.state is not None
        assert (load.state.version, load.state.workflow, load.state.step) == (
            previous.version,
            previous.workflow,
            previous.step,
        )
        assert [entry.step for entry in load.state.history] == ["step_0", "step_1", "step_2"]

    def test_writes_no_handoff_without_a_previous_state(self, tmp_path, monkeypatch):
        from nebuko.engine.paths import repo_root
        from nebuko.engine.run_loop import Engine
        from workflow_state.state_codec import WorkflowStateCodec

        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        adapter = _adapter(tmp_path, FakeGhClient())
        engine = Engine(adapter=adapter, repo_root=repo_root(), env={})

        assert engine.stage_prev_state() is None
        assert WorkflowStateCodec().load_prev().status == "absent"

    def test_removes_a_stale_handoff_without_a_previous_state(self, tmp_path, monkeypatch):
        """A stale file from a previous job must not survive a fresh start.

        ``$RUNNER_TEMP`` is emptied per job only on ephemeral runners; on a
        reused runner the previous job's ``prev_workflow_state.json`` would
        otherwise be adopted, so ``stage_prev_state`` deletes it (issue #739).
        """
        from nebuko.engine.paths import repo_root
        from nebuko.engine.run_loop import Engine
        from workflow_state.state_codec import WorkflowStateCodec

        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        previous, _ = _state_comment(history_len=2)
        WorkflowStateCodec().save_prev(previous)
        assert WorkflowStateCodec().load_prev().status == "ok"
        adapter = _adapter(tmp_path, FakeGhClient())
        engine = Engine(adapter=adapter, repo_root=repo_root(), env={})

        assert engine.stage_prev_state() is None

        assert not (tmp_path / "prev_workflow_state.json").exists()
        assert WorkflowStateCodec().load_prev().status == "absent"


class TestHistoryIncrementRegression:
    """The seeded hand-off makes the child's history increment (issues #726/#739).

    End-to-end over the real seams the child uses: ``stage_prev_state`` writes
    the codec hand-off, the codec reads it back, and ``build_state`` advances it.
    """

    def test_the_recovered_state_advances_the_childs_history(self, tmp_path, monkeypatch):
        from nebuko.engine.paths import repo_root
        from nebuko.engine.run_loop import Engine
        from workflow_state.features.build_state.build_state import build_state
        from workflow_state.state_codec import WorkflowStateCodec

        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        previous, body = _state_comment(history_len=3)
        adapter = _adapter(
            tmp_path, FakeGhClient(comments=[{"body": body, "user": {"login": "x"}}])
        )
        engine = Engine(adapter=adapter, repo_root=repo_root(), env={})

        engine.stage_prev_state()
        recovered = WorkflowStateCodec().load_prev()

        assert recovered.status == "ok"
        new_state = build_state(
            recovered.state,
            workflow="spec-driven-impl",
            step="stub",
            agent="dev",
            summary="stub posted",
        )

        assert new_state.version == previous.version + 1
        assert len(new_state.history) == len(previous.history) + 1
        assert (new_state.workflow, new_state.step) == ("spec-driven-impl", "stub")
        assert new_state.history[-1].step == "stub"

    def test_without_a_handoff_the_child_resets_the_history(self):
        """The pre-fix shape from #739: ``build_state(prev_state=None)``."""
        from workflow_state.features.build_state.build_state import build_state

        state = build_state(None, workflow="", step="", agent="genius", summary="s")

        assert state.version == 3
        assert len(state.history) == 1
        assert state.history[0].step == ""
