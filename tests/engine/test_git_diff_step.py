"""The example workflow's diff step (issue #972, simplified by #1327).

``git_diff`` used to fetch ``origin/main`` itself, because every script step ran
on a fresh, shallow, ref-pinned checkout (#957 → #965 → #972). Since #1327 the
engine checks the session branch out for script steps, with enough history for
the three-dot diff, so the step is a plain diff and the fetch is gone.

The test drives the workflow's *declared* argv — read back through
``load_workflow``, so it cannot drift from what ships — against a real scratch
repository, after the engine's own evidence checkout. Mocking git would test
nothing.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from nebuko.engine.lib.config import load_workflow
from nebuko.engine.lib.git.evidence_checkout import resolve_evidence_checkout

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _declared_step(
    workflow: str = "plan-dev-review-cmd",
    directory: str = ".nebuko/workflows",
):
    path = REPO_ROOT / directory / f"{workflow}.yaml"
    definition = load_workflow(str(path))
    step = next(s for s in definition.steps if s.name == "git_diff")
    assert step.script is not None
    return step.script


def _declared_argv(
    workflow: str = "plan-dev-review-cmd", directory: str = ".nebuko/workflows"
) -> list[str]:
    script = _declared_step(workflow, directory=directory)
    return [script.command, *(script.args or [])]


def _shallow_checkout(tmp_path: Path, base: str = "main") -> Path:
    """A checkout shaped like CI's: one commit ahead of ``base``, no ``origin/<base>``.

    ``actions/checkout`` fetches only the triggering ref, so the remote-tracking
    ``origin/<base>`` a plain ``git diff origin/<base> HEAD`` needs never exists.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", base, str(remote)], check=True
    )

    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", base, str(seed)], check=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())

    (seed / "file.txt").write_text("base line\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", base)

    (seed / "file.txt").write_text("base line\nahead line\n")
    _git(seed, "commit", "-q", "-am", "ahead")
    _git(seed, "push", "-q", "origin", "HEAD:refs/heads/feature/issue-1-x")

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", "-b", base, str(checkout)], check=True)
    _git(checkout, "remote", "add", "origin", remote.as_uri())
    _git(checkout, "fetch", "-q", "--depth=1", "origin", "feature/issue-1-x")
    _git(checkout, "checkout", "-q", "--detach", "FETCH_HEAD")
    return checkout


@pytest.fixture()
def shallow_checkout(tmp_path: Path) -> Path:
    return _shallow_checkout(tmp_path)


def _shipped_argv() -> list[str]:
    return _declared_argv("dev-review-loop", directory="defaults/workflows")


class TestGitDiffStep:
    def test_the_step_runs_on_the_session_checkout(self):
        assert _declared_step().checkout == "session"

    def test_the_step_opts_out_of_the_merge_before_tests(self):
        """Issue #1344: a merge would pull the base branch into the change under review."""
        for name in ("dev-review-loop", "default"):
            step = _declared_step(name, directory="defaults/workflows")

            assert step.env == {"NEBUKO_MERGE_MAIN": "0"}

    def test_the_shipped_diff_is_taken_against_the_configured_base_branch(self):
        for name in ("dev-review-loop", "default"):
            script = _declared_step(name, directory="defaults/workflows")

            joined = " ".join(script.args)

            assert '"origin/$base...HEAD"' in joined
            assert "origin/main" not in joined

    def test_a_repo_whose_default_branch_is_not_main_gets_its_own_diff(
        self, tmp_path: Path
    ):
        checkout = _shallow_checkout(tmp_path, base="trunk")
        env = {"NEBUKO_BASE_BRANCH": "trunk"}
        assert resolve_evidence_checkout(
            checkout, env, mode="session", branch="feature/issue-1-x"
        ).ok

        result = subprocess.run(
            _shipped_argv(), cwd=checkout, capture_output=True, text=True,
            env={**os.environ, **env},
        )

        assert result.returncode == 0, result.stderr
        assert "+ahead line" in result.stdout

    def test_without_a_base_branch_the_remote_default_is_used_as_the_manifest_does(
        self, tmp_path: Path
    ):
        """No ``NEBUKO_BASE_BRANCH``: the diff follows ``origin/HEAD``, as the manifest ref does."""
        checkout = _shallow_checkout(tmp_path, base="trunk")
        _git(checkout, "fetch", "-q", "origin", "trunk")
        _git(checkout, "remote", "set-head", "origin", "trunk")
        env = {k: v for k, v in os.environ.items() if k != "NEBUKO_BASE_BRANCH"}
        assert resolve_evidence_checkout(
            checkout, {}, mode="session", branch="feature/issue-1-x"
        ).ok

        result = subprocess.run(
            _shipped_argv(), cwd=checkout, capture_output=True, text=True, env=env,
        )

        assert result.returncode == 0, result.stderr
        assert "+ahead line" in result.stdout

    def test_a_hostile_base_branch_is_passed_as_one_inert_ref(self, tmp_path: Path):
        checkout = _shallow_checkout(tmp_path)
        marker = checkout / "PWNED"
        env = {**os.environ, "NEBUKO_BASE_BRANCH": "main; touch PWNED"}

        result = subprocess.run(
            _shipped_argv(), cwd=checkout, capture_output=True, text=True, env=env
        )

        assert result.returncode != 0
        assert not marker.exists()

    def test_the_repo_manifest_declares_the_merge_default(self):
        manifest = yaml.safe_load(
            (REPO_ROOT / ".nebuko" / "nebuko.yaml").read_text(encoding="utf-8")
        )

        assert manifest["merge_main"] is True

    def test_the_engine_checkout_makes_the_three_dot_diff_resolve(
        self, shallow_checkout: Path
    ):
        checkout = resolve_evidence_checkout(
            shallow_checkout, {}, mode="session", branch="feature/issue-1-x"
        )
        assert checkout.ok, checkout.detail

        result = subprocess.run(
            _declared_argv(), cwd=shallow_checkout, capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr
        assert "+ahead line" in result.stdout

    def test_a_bare_diff_would_die_without_the_engine_checkout(
        self, shallow_checkout: Path
    ):
        """The #957 mechanism the engine-side checkout exists to prevent."""
        result = subprocess.run(
            _declared_argv(), cwd=shallow_checkout, capture_output=True, text=True
        )

        assert result.returncode == 128
        assert "ambiguous argument" in result.stderr
