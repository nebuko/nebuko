"""Script steps and exec inputs run on the session branch (issue #1327).

Real git against a local bare remote: a script step after ``implement`` runs on
the exact pushed SHA, reports it, and an unreachable branch fails the run
before the command executes. ``checkout: workflow`` keeps the old behaviour.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState
from nebuko.engine.models import DeterministicStep, ExitCode, Trigger
from nebuko.engine.paths import repo_root
from nebuko.engine.run_loop import EVIDENCE_COMMITS_KEY, Engine

BRANCH = "feature/issue-1327-session-checkout"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A depth-1 checkout of ``main`` plus a pushed session branch on the remote."""
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())
    _commit(seed, "base.txt", "base\n", "base")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "checkout", "-q", "-b", BRANCH)
    _commit(seed, "work.txt", "work\n", "work")
    _git(seed, "push", "-q", "origin", BRANCH)

    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", "--branch", "main", remote.as_uri(), str(work)],
        check=True,
    )
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@example.com")
    return work


def _pushed_sha(workspace: Path, branch: str = BRANCH) -> str:
    """The SHA the remote holds for ``branch`` (the shallow clone lacks the ref)."""
    return _git(workspace, "ls-remote", "origin", f"refs/heads/{branch}").split()[0]


def _patch_resolver(monkeypatch, resolved: dict) -> None:
    import nebuko.engine.lib.resolve_config  # noqa: F401 - import the submodule

    monkeypatch.setattr(
        sys.modules["nebuko.engine.lib.resolve_config.resolve_config"],
        "resolve_config",
        lambda **_: resolved,
    )


def _engine(tmp_path: Path, workspace: Path, monkeypatch) -> Engine:
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    adapter = LocalAdapter(
        tmp_path / "runs" / "run-1",
        trigger=Trigger(
            author="olivier-norture", body="@@dev fix it", type="issue_comment", number=590
        ),
    )
    return Engine(
        adapter=adapter,
        repo_root=repo_root(),
        env={
            "CIRCUIT_BREAKER_THRESHOLD": "3",
            "GITHUB_REPOSITORY": "olivier-norture/git-flow-ai",
            "GITHUB_WORKSPACE": str(workspace),
        },
    )


def _script_resolved(command: str, args: list[str], **extra) -> dict:
    resolved = {
        "SKIP_AGENT": "true",
        "RUN_SCRIPT": "true",
        "SCRIPT_PATH": command,
        "SCRIPT_ARGS": json.dumps(args),
        "WORKFLOW_STEP": "run_tests",
        "WORKFLOW_NAME": "default",
    }
    resolved.update(extra)
    return resolved


def _seed_state(engine: Engine, *, branch: str = "", **context) -> None:
    git = {"branch_name": branch} if branch else None
    engine.adapter.save_state(
        WorkflowState.model_validate(
            {
                "workflow": "default",
                "step": "implement",
                "agent": "dev",
                "current_context": context,
                "git": git,
                # A real state after an agent step carries its history entry;
                # the branch recovery only applies to an agent session.
                "history": [
                    {
                        "step": "implement",
                        "agent": "dev",
                        "turn_outcome": {
                            "status": "completed",
                            "appreciation": "approved",
                        },
                    }
                ],
            }
        )
    )


class TestSessionCheckout:
    def test_runs_on_the_pushed_sha_and_reports_it(
        self, tmp_path, workspace, monkeypatch
    ):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, branch=BRANCH)
        _patch_resolver(
            monkeypatch,
            _script_resolved("sh", ["-c", "git rev-parse HEAD"]),
        )
        pushed = _pushed_sha(workspace)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert pushed in outcome.posted_body
        assert f"branch: `{BRANCH}`" in outcome.posted_body
        assert f"`{pushed[:12]}`" in outcome.posted_body

        saved = engine.adapter.load_state()
        assert saved.current_context[EVIDENCE_COMMITS_KEY]["run_tests"] == {
            "branch": BRANCH,
            "head_sha": pushed,
        }

    def test_the_diff_resolves_from_a_shallow_checkout(
        self, tmp_path, workspace, monkeypatch
    ):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, branch=BRANCH)
        _patch_resolver(
            monkeypatch,
            _script_resolved("sh", ["-c", "git diff --stat origin/main...HEAD"]),
        )

        outcome = engine.run()

        assert "work.txt" in outcome.posted_body

    def test_a_first_run_reports_branch_none(self, tmp_path, workspace, monkeypatch):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _patch_resolver(monkeypatch, _script_resolved("sh", ["-c", "echo hi"]))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert "branch: `none`" in outcome.posted_body
        saved = engine.adapter.load_state()
        assert saved.current_context[EVIDENCE_COMMITS_KEY]["run_tests"]["branch"] == ""

    def test_an_unreachable_branch_fails_before_the_command(
        self, tmp_path, workspace, monkeypatch
    ):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, branch="feature/does-not-exist")
        marker = tmp_path / "ran"
        _patch_resolver(
            monkeypatch,
            _script_resolved("sh", ["-c", f"touch {marker}"]),
        )

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR
        assert "evidence checkout failed" in outcome.posted_body
        assert not marker.exists()


class TestUnrecordedBranchRecovery:
    """A standalone evidence step on an issue whose branch already exists (#1397).

    The state may predate the branch record (or have been posted by an older
    engine), so the branch is recovered from ``origin/feature/issue-<n>-*``
    instead of silently reporting ``branch: none``.
    """

    def _push_issue_branch(self, workspace: Path, name: str) -> None:
        _git(workspace, "push", "-q", "origin", f"HEAD:refs/heads/{name}")

    def test_the_only_issue_branch_is_used(
        self, tmp_path, workspace, monkeypatch
    ):
        self._push_issue_branch(workspace, "feature/issue-590-recovered")
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine)
        _patch_resolver(
            monkeypatch,
            _script_resolved("sh", ["-c", "git rev-parse HEAD"]),
        )

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert "branch: `feature/issue-590-recovered`" in outcome.posted_body

    def test_two_issue_branches_fail_instead_of_guessing(
        self, tmp_path, workspace, monkeypatch
    ):
        self._push_issue_branch(workspace, "feature/issue-590-one")
        self._push_issue_branch(workspace, "feature/issue-590-two")
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine)
        marker = tmp_path / "ran"
        _patch_resolver(
            monkeypatch,
            _script_resolved("sh", ["-c", f"touch {marker}"]),
        )

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR
        assert "several" in outcome.posted_body
        assert not marker.exists()


class TestWorkflowCheckout:
    def test_keeps_the_workflow_ref(self, tmp_path, workspace, monkeypatch):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, branch=BRANCH)
        _patch_resolver(
            monkeypatch,
            _script_resolved(
                "sh", ["-c", "git rev-parse HEAD"], SCRIPT_CHECKOUT="workflow"
            ),
        )
        workflow_head = _git(workspace, "rev-parse", "HEAD")

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert workflow_head in outcome.posted_body
        assert "branch: `main`" in outcome.posted_body


class TestExecInputs:
    def test_exec_inputs_run_in_the_session_checkout(
        self, tmp_path, workspace, monkeypatch
    ):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, branch=BRANCH)
        resolved = {
            "SKIP_AGENT": "true",
            "REASON": "no next step",
            "SKIP_KIND": "no_next_agent",
            "STEP_INPUTS": json.dumps(
                {"consumer": {"head": {"exec": ["git", "rev-parse", "HEAD"]}}}
            ),
        }
        _patch_resolver(monkeypatch, resolved)

        _results, failure = engine._materialize_inputs(
            resolved, ["normalize", "resolve_config", "guards", "action"]
        )

        assert failure is None
        assert _git(workspace, "rev-parse", "HEAD") == _pushed_sha(workspace)


class TestMergeBaseBeforeTests:
    """The ``merge_main`` default and the per-step opt-out (issue #1344).

    CI tests GitHub's pull-request merge commit; a step that ran on the bare
    branch head reported failures ``main`` had already fixed (31 local vs 4 in
    CI on the same commit).
    """

    def _step(self, **env: str) -> DeterministicStep:
        return DeterministicStep.from_resolved(
            _script_resolved("sh", ["-c", "true"], SCRIPT_ENV=json.dumps(env))
        )

    def test_the_default_is_on(self, tmp_path, workspace, monkeypatch):
        engine = _engine(tmp_path, workspace, monkeypatch)

        assert engine._merge_base_requested(self._step()) is True

    def test_the_manifest_can_turn_it_off(
        self, tmp_path, workspace, monkeypatch
    ):
        (workspace / ".nebuko").mkdir(exist_ok=True)
        (workspace / ".nebuko" / "nebuko.yaml").write_text("merge_main: false\n")
        engine = _engine(tmp_path, workspace, monkeypatch)

        assert engine._merge_base_requested(self._step()) is False

    def test_the_step_env_overrides_the_manifest_both_ways(
        self, tmp_path, workspace, monkeypatch
    ):
        (workspace / ".nebuko").mkdir(exist_ok=True)
        (workspace / ".nebuko" / "nebuko.yaml").write_text("merge_main: false\n")
        engine = _engine(tmp_path, workspace, monkeypatch)

        assert engine._merge_base_requested(
            self._step(NEBUKO_MERGE_MAIN="1")
        ) is True
        (workspace / ".nebuko" / "nebuko.yaml").write_text("merge_main: true\n")
        engine = _engine(tmp_path, workspace, monkeypatch)
        assert engine._merge_base_requested(
            self._step(NEBUKO_MERGE_MAIN="0")
        ) is False

    def test_run_tests_tests_the_merged_tree_and_says_so(
        self, tmp_path, workspace, monkeypatch
    ):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, branch=BRANCH)
        _patch_resolver(
            monkeypatch,
            _script_resolved("sh", ["-c", "git rev-parse HEAD"]),
        )
        branch_head = _pushed_sha(workspace)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert f"merge of {branch_head} into " in outcome.posted_body
        # The merge commit is local only: the remote branch never moved.
        assert _pushed_sha(workspace) == branch_head

    def test_an_opted_out_step_tests_the_branch_head(
        self, tmp_path, workspace, monkeypatch
    ):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, branch=BRANCH)
        _patch_resolver(
            monkeypatch,
            _script_resolved(
                "sh",
                ["-c", "git rev-parse HEAD"],
                SCRIPT_ENV=json.dumps({"NEBUKO_MERGE_MAIN": "0"}),
            ),
        )
        branch_head = _pushed_sha(workspace)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert f"branch head {branch_head}" in outcome.posted_body
        assert "merge of" not in outcome.posted_body


class TestSingleJobHandOff:
    """Lane D: a later step of the in-process loop has no persisted state yet.

    The previous step's state is handed over in memory, so the evidence checkout
    must still resolve the session branch from it (issue #1475).
    """

    def test_a_later_loop_step_checks_out_the_handed_over_branch(
        self, tmp_path, workspace, monkeypatch
    ):
        engine = _engine(tmp_path, workspace, monkeypatch)
        handed = WorkflowState.model_validate(
            {
                "workflow": "default",
                "step": "implement",
                "agent": "dev",
                "current_context": {},
                "git": {"branch_name": BRANCH},
                "history": [
                    {
                        "step": "implement",
                        "agent": "dev",
                        "turn_outcome": {"status": "completed", "appreciation": "approved"},
                    }
                ],
            }
        )
        monkeypatch.setattr("nebuko.engine.run_loop.handed_over_state", lambda: handed)
        _patch_resolver(
            monkeypatch,
            _script_resolved("sh", ["-c", "git rev-parse HEAD"]),
        )
        pushed = _pushed_sha(workspace)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert f"branch: `{BRANCH}`" in outcome.posted_body
        assert pushed in outcome.posted_body

    def test_run_tests_reads_the_manifest_from_the_base_ref(
        self, tmp_path, workspace, monkeypatch
    ):
        """The in-process loop must read ``test_command`` from ``origin/main``, not the checkout."""
        remote = _git(workspace, "remote", "get-url", "origin")
        publisher = tmp_path / "publisher"
        subprocess.run(["git", "clone", "-q", remote, str(publisher)], check=True)
        _git(publisher, "config", "user.name", "t")
        _git(publisher, "config", "user.email", "t@example.com")
        (publisher / ".nebuko").mkdir()
        (publisher / ".nebuko" / "nebuko.yaml").write_text("test_command: echo committed-cmd\n")
        _git(publisher, "add", "-A")
        _git(publisher, "commit", "-q", "-m", "manifest")
        _git(publisher, "push", "-q", "origin", "HEAD:main")
        _git(workspace, "fetch", "-q", "origin", "main")
        (workspace / ".nebuko").mkdir(exist_ok=True)
        (workspace / ".nebuko" / "nebuko.yaml").write_text("test_command: echo edited-cmd\n")
        monkeypatch.delenv("NEBUKO_BASE_BRANCH", raising=False)
        monkeypatch.delenv("NEBUKO_TEST_COMMAND", raising=False)
        engine = _engine(tmp_path, workspace, monkeypatch)
        handed = WorkflowState.model_validate(
            {
                "workflow": "default",
                "step": "implement",
                "agent": "dev",
                "current_context": {},
                "git": {"branch_name": BRANCH},
                "history": [
                    {
                        "step": "implement",
                        "agent": "dev",
                        "turn_outcome": {"status": "completed", "appreciation": "approved"},
                    }
                ],
            }
        )
        monkeypatch.setattr("nebuko.engine.run_loop.handed_over_state", lambda: handed)
        _patch_resolver(
            monkeypatch,
            _script_resolved(
                "sh",
                ["-c", 'echo "RAN:${NEBUKO_TEST_COMMAND}"'],
                SCRIPT_ENV=json.dumps({"NEBUKO_MERGE_MAIN": "0"}),
            ),
        )

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert "RAN:echo committed-cmd" in outcome.posted_body
        assert "edited-cmd" not in outcome.posted_body
