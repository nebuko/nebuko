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
from nebuko.engine.models import ExitCode, Trigger
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


def _seed_state(engine: Engine, **context) -> None:
    engine.adapter.save_state(
        WorkflowState.model_validate(
            {
                "workflow": "default",
                "step": "implement",
                "agent": "dev",
                "current_context": context,
            }
        )
    )


class TestSessionCheckout:
    def test_runs_on_the_pushed_sha_and_reports_it(
        self, tmp_path, workspace, monkeypatch
    ):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, dev_branch=BRANCH)
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
        _seed_state(engine, dev_branch=BRANCH)
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
        _seed_state(engine, dev_branch="feature/does-not-exist")
        marker = tmp_path / "ran"
        _patch_resolver(
            monkeypatch,
            _script_resolved("sh", ["-c", f"touch {marker}"]),
        )

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR
        assert "evidence checkout failed" in outcome.posted_body
        assert not marker.exists()


class TestWorkflowCheckout:
    def test_keeps_the_workflow_ref(self, tmp_path, workspace, monkeypatch):
        engine = _engine(tmp_path, workspace, monkeypatch)
        _seed_state(engine, dev_branch=BRANCH)
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
        _seed_state(engine, dev_branch=BRANCH)
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
