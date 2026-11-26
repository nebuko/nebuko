"""The shipped ``git_diff`` / ``run_tests`` evidence steps (issue #1475).

Both steps are read back through ``load_workflow`` from ``defaults/workflows``,
so the tests run what ships. The ``run_tests`` tests execute against a real
repository whose committed ``.nebuko/nebuko.yaml`` is the trusted copy.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nebuko.engine.lib.config import load_workflow
from nebuko.engine.models import DeterministicStep, ExitCode, RunOutcome, _manifest_overlay
from nebuko.engine.step_driver import DriverConfig, StepDriver

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED = REPO_ROOT / "defaults" / "workflows"
NO_COMMAND = "no test_command configured in .nebuko/nebuko.yaml"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(tmp_path: Path, committed: str | None, working: str | None) -> Path:
    """A session checkout on a feature branch; ``committed`` is on ``origin/main``.

    ``working`` replaces the manifest in the checkout's working tree, the way
    an edit on the branch under review would.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())
    (seed / "README.md").write_text("x\n")
    if committed is not None:
        (seed / ".nebuko").mkdir()
        (seed / ".nebuko" / "nebuko.yaml").write_text(committed)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", remote.as_uri(), str(checkout)], check=True)
    _git(checkout, "config", "user.name", "t")
    _git(checkout, "config", "user.email", "t@example.com")
    _git(checkout, "checkout", "-q", "-b", "feature/issue-1-x")
    if working is not None:
        (checkout / ".nebuko").mkdir(exist_ok=True)
        (checkout / ".nebuko" / "nebuko.yaml").write_text(working)
    return checkout


def _publish_branch(checkout: Path, name: str, manifest: str) -> None:
    """Push ``name`` to origin with ``manifest`` committed, then leave the checkout on the feature branch."""
    _git(checkout, "checkout", "-q", "-b", name, "origin/main")
    (checkout / ".nebuko" / "nebuko.yaml").write_text(manifest)
    _git(checkout, "commit", "-q", "-am", name)
    _git(checkout, "push", "-q", "origin", name)
    _git(checkout, "checkout", "-q", "feature/issue-1-x")


def _shipped_script(workflow: str, step_name: str):
    definition = load_workflow(str(SHIPPED / f"{workflow}.yaml"))
    return next(s for s in definition.steps if s.name == step_name).script


def _evidence_step(step_name: str, script_env: dict[str, str] | None = None, timeout: int = 20) -> DeterministicStep:
    script = _shipped_script("dev-review-loop", step_name)
    return DeterministicStep(
        kind="run_script",
        script_path=script.command,
        script_args=json.dumps(script.args or []),
        script_env=json.dumps(script_env or {}),
        timeout_minutes=timeout,
        step_name=step_name,
    )


def _run_tests_step(script_env: dict[str, str] | None = None, timeout: int = 20) -> DeterministicStep:
    return _evidence_step("run_tests", script_env, timeout)


def _rev(repo: Path, ref: str) -> str:
    return subprocess.run(["git", "rev-parse", ref], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def engine_env(monkeypatch):
    """No inherited test settings: only the checkout's manifest may supply them."""
    monkeypatch.delenv("NEBUKO_TEST_COMMAND", raising=False)
    monkeypatch.delenv("NEBUKO_TEST_TIMEOUT_MINUTES", raising=False)
    monkeypatch.delenv("NEBUKO_BASE_BRANCH", raising=False)


class TestShippedWorkflowsReachReviewThroughEvidence:
    @pytest.mark.parametrize("workflow", ["default", "dev-review-loop"])
    def test_implement_runs_git_diff_then_run_tests_then_review(self, workflow):
        steps = {s["name"]: s for s in yaml.safe_load((SHIPPED / f"{workflow}.yaml").read_text())["steps"]}

        assert steps["implement"]["next"] == "git_diff"
        assert steps["git_diff"]["conditions"] == [{"on_result": "default", "next": "run_tests"}]
        assert steps["run_tests"]["conditions"] == [{"on_result": "default", "next": "review"}]
        assert steps["review"]["inputs"] == ["implement", "git_diff", "run_tests"]
        assert steps["git_diff"]["script"]["env"] == {"NEBUKO_MERGE_MAIN": "0"}
        # Tests the same tree git_diff shows, so the review's head check holds.
        assert steps["run_tests"]["script"]["env"] == {"NEBUKO_MERGE_MAIN": "0"}

    def test_the_review_prompt_trusts_the_evidence_and_refuses_pre_existing_claims(self):
        for workflow in ("default", "dev-review-loop"):
            steps = {s["name"]: s for s in yaml.safe_load((SHIPPED / f"{workflow}.yaml").read_text())["steps"]}
            prompt = " ".join(steps["review"]["prompt"].split())

            assert "run_tests" in prompt
            assert "no test_command configured" in prompt
            assert "no test_command was checked" in prompt
            assert "on the base branch" in prompt
            assert "Do not run the whole test suite" in prompt


class TestRunTestsStep:
    def test_without_a_test_command_it_reports_that_and_passes(self, tmp_path, monkeypatch, engine_env):
        checkout = _repo(tmp_path, committed="base_branch: main\n", working=None)
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))

        result = _run_tests_step().execute(adapter=None)

        assert result.ok
        assert NO_COMMAND in result.output

    def test_a_blank_command_counts_as_none(self, tmp_path, monkeypatch, engine_env):
        checkout = _repo(tmp_path, committed='test_command: "   "\n', working=None)
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))

        result = _run_tests_step().execute(adapter=None)

        assert result.ok
        assert NO_COMMAND in result.output

    def test_a_failing_command_is_evidence_with_its_output(self, tmp_path, monkeypatch, engine_env):
        checkout = _repo(tmp_path, committed="test_command: echo FAIL-MARK && false\n", working=None)
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))

        result = _run_tests_step().execute(adapter=None)

        assert not result.ok
        assert "FAIL-MARK" in result.output

    def test_an_edit_of_the_manifest_on_the_branch_does_not_change_what_runs(
        self, tmp_path, monkeypatch, engine_env
    ):
        checkout = _repo(
            tmp_path,
            committed="test_command: echo committed-cmd\n",
            working="test_command: echo edited-cmd\n",
        )
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))

        result = _run_tests_step().execute(adapter=None)

        assert result.ok
        assert "committed-cmd" in result.output
        assert "edited-cmd" not in result.output

    def test_a_manifest_that_is_only_on_the_branch_is_not_trusted(
        self, tmp_path, monkeypatch, engine_env
    ):
        checkout = _repo(tmp_path, committed=None, working="test_command: echo edited-cmd\n")
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))

        result = _run_tests_step().execute(adapter=None)

        assert NO_COMMAND in result.output
        assert "edited-cmd" not in result.output

    def test_a_base_named_on_the_branch_does_not_pick_the_ref(self, tmp_path, monkeypatch, engine_env):
        checkout = _repo(tmp_path, committed="test_command: echo committed-cmd\n", working=None)
        _publish_branch(checkout, "evil", "test_command: echo evil-cmd\n")
        (checkout / ".nebuko" / "nebuko.yaml").write_text(
            "base_branch: evil\ntest_command: echo edited-cmd\n"
        )
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))

        result = _run_tests_step().execute(adapter=None)

        assert result.ok
        assert "committed-cmd" in result.output
        assert "evil-cmd" not in result.output
        assert "edited-cmd" not in result.output

    def test_the_engine_base_branch_selects_the_ref(self, tmp_path, monkeypatch, engine_env):
        checkout = _repo(tmp_path, committed="test_command: echo committed-cmd\n", working=None)
        _publish_branch(checkout, "evil", "test_command: echo evil-cmd\n")
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))
        monkeypatch.setenv("NEBUKO_BASE_BRANCH", "evil")

        result = _run_tests_step().execute(adapter=None)

        assert "evil-cmd" in result.output
        assert "committed-cmd" not in result.output

    def test_a_base_not_fetched_is_reported_as_unreadable_not_as_none(
        self, tmp_path, monkeypatch, engine_env, caplog
    ):
        checkout = _repo(tmp_path, committed="test_command: echo committed-cmd\n", working=None)
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))
        monkeypatch.setenv("NEBUKO_BASE_BRANCH", "not-fetched")

        with caplog.at_level(logging.WARNING):
            result = _run_tests_step().execute(adapter=None)

        assert result.ok
        assert "could not be read from origin/not-fetched" in result.output
        assert NO_COMMAND not in result.output
        assert "could not read .nebuko/nebuko.yaml from origin/not-fetched" in caplog.text

    def test_an_invalid_committed_manifest_is_reported_as_invalid(
        self, tmp_path, monkeypatch, engine_env, caplog
    ):
        checkout = _repo(tmp_path, committed='base_branch: "../evil"\n', working=None)
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))

        with caplog.at_level(logging.WARNING):
            result = _run_tests_step().execute(adapter=None)

        assert result.ok
        assert "is invalid" in result.output
        assert NO_COMMAND not in result.output
        assert "is invalid" in caplog.text

    def test_an_explicit_step_env_beats_the_committed_manifest(
        self, tmp_path, monkeypatch, engine_env
    ):
        checkout = _repo(tmp_path, committed="test_command: echo committed-cmd\n", working=None)
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))

        result = _run_tests_step({"NEBUKO_TEST_COMMAND": "echo step-cmd"}).execute(adapter=None)

        assert "step-cmd" in result.output
        assert "committed-cmd" not in result.output

    def test_the_manifest_timeout_applies_to_run_tests_only(self, tmp_path, monkeypatch, engine_env):
        checkout = _repo(tmp_path, committed="test_timeout_minutes: 2\n", working=None)
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))
        env = _manifest_overlay(checkout)

        assert env is not None
        assert _run_tests_step()._timeout_minutes_for(env) == 2
        other = DeterministicStep(kind="run_script", script_path="true", timeout_minutes=20, step_name="git_diff")
        assert other._timeout_minutes_for(env) == 20


class TestBaseSnapshotInTheSingleJobLoop:
    def test_an_agent_step_cannot_repoint_the_base_that_script_steps_read(
        self, tmp_path, monkeypatch, engine_env
    ):
        """Issue #1475: the base is resolved before the first agent step and kept for the run."""
        checkout = _repo(tmp_path, committed="test_command: echo committed-cmd\n", working=None)
        (checkout / "feature.txt").write_text("feature\n")
        _git(checkout, "add", "-A")
        _git(checkout, "commit", "-q", "-m", "feature")
        monkeypatch.setenv("GITHUB_WORKSPACE", str(checkout))
        results = {}

        def agent_step():
            # The dev agent's edit in the shared checkout: a commit on top of the
            # branch that carries a malicious manifest, with the base refs moved to it.
            _git(checkout, "checkout", "-q", "--detach")
            (checkout / ".nebuko").mkdir(exist_ok=True)
            (checkout / ".nebuko" / "nebuko.yaml").write_text("test_command: echo evil-cmd\n")
            (checkout / "evil.txt").write_text("evil\n")
            _git(checkout, "add", "-A")
            _git(checkout, "commit", "-q", "-m", "evil")
            evil = _rev(checkout, "HEAD")
            _git(checkout, "update-ref", "refs/remotes/origin/evil", evil)
            _git(checkout, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/evil")
            _git(checkout, "update-ref", "refs/remotes/origin/main", evil)
            _git(checkout, "checkout", "-q", "feature/issue-1-x")
            return _outcome(ExitCode.ADVANCE)

        def script_steps():
            results["run_tests"] = _run_tests_step().execute(adapter=None)
            results["git_diff"] = _evidence_step("git_diff", {"NEBUKO_MERGE_MAIN": "0"}).execute(adapter=None)
            return _outcome(ExitCode.HOLD)

        driver = StepDriver(
            DriverConfig(enabled=True, step_budget_s=1200.0, max_steps=6, wall_clock_s=3000.0),
            engine_factory=lambda index: SimpleNamespace(run=agent_step if index == 1 else script_steps),
            adapter_probe=frozenset,
            clock=lambda: 0.0,
            notify=lambda _body: None,
            origin=0.0,
        )

        driver.run()

        assert results["run_tests"].ok
        assert "committed-cmd" in results["run_tests"].output
        assert "evil-cmd" not in results["run_tests"].output
        assert "feature.txt" in results["git_diff"].output
        assert "evil.txt" not in results["git_diff"].output


def _outcome(code: ExitCode) -> RunOutcome:
    return RunOutcome(exit_code=code, turn_outcome=None, prompt=None, posted_body=None, state=None, phases=())
