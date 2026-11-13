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

import subprocess
from pathlib import Path

import pytest

from nebuko.engine.lib.config import load_workflow
from nebuko.engine.lib.git.evidence_checkout import resolve_evidence_checkout

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_WORKFLOW = REPO_ROOT / ".nebuko" / "workflows" / "plan-dev-review-cmd.yaml"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _declared_step():
    definition = load_workflow(str(EXAMPLE_WORKFLOW))
    step = next(s for s in definition.steps if s.name == "git_diff")
    assert step.script is not None
    return step.script


def _declared_argv() -> list[str]:
    script = _declared_step()
    return [script.command, *(script.args or [])]


@pytest.fixture()
def shallow_checkout(tmp_path: Path) -> Path:
    """A checkout shaped like CI's: one commit ahead of ``main``, no ``origin/main``.

    ``actions/checkout`` fetches only the triggering ref, so the remote-tracking
    ``origin/main`` a plain ``git diff origin/main HEAD`` needs never exists.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True
    )

    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())

    (seed / "file.txt").write_text("base line\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")

    (seed / "file.txt").write_text("base line\nahead line\n")
    _git(seed, "commit", "-q", "-am", "ahead")
    _git(seed, "push", "-q", "origin", "HEAD:refs/heads/feature/issue-1-x")

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    _git(checkout, "remote", "add", "origin", remote.as_uri())
    _git(checkout, "fetch", "-q", "--depth=1", "origin", "feature/issue-1-x")
    _git(checkout, "checkout", "-q", "--detach", "FETCH_HEAD")
    return checkout


class TestGitDiffStep:
    def test_the_step_runs_on_the_session_checkout(self):
        assert _declared_step().checkout == "session"

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
