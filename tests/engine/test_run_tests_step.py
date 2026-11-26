"""The shipped ``git_diff`` / ``run_tests`` evidence steps (issue #1475).

Both steps are read back through ``load_workflow`` from ``defaults/workflows``,
so the tests run what ships. The ``run_tests`` tests execute against a real
repository whose committed ``.nebuko/nebuko.yaml`` is the trusted copy.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from nebuko.engine.lib.config import load_workflow
from nebuko.engine.models import DeterministicStep, _manifest_overlay

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


def _shipped_script(workflow: str, step_name: str):
    definition = load_workflow(str(SHIPPED / f"{workflow}.yaml"))
    return next(s for s in definition.steps if s.name == step_name).script


def _run_tests_step(script_env: dict[str, str] | None = None, timeout: int = 20) -> DeterministicStep:
    script = _shipped_script("dev-review-loop", "run_tests")
    return DeterministicStep(
        kind="run_script",
        script_path=script.command,
        script_args=json.dumps(script.args or []),
        script_env=json.dumps(script_env or {}),
        timeout_minutes=timeout,
        step_name="run_tests",
    )


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

    def test_the_review_prompt_trusts_the_evidence_and_refuses_pre_existing_claims(self):
        for workflow in ("default", "dev-review-loop"):
            steps = {s["name"]: s for s in yaml.safe_load((SHIPPED / f"{workflow}.yaml").read_text())["steps"]}
            prompt = steps["review"]["prompt"]

            assert "run_tests" in prompt
            assert "no test_command is configured" in prompt
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
