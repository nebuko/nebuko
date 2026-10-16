"""The example workflow's combined fetch+diff step (issue #972).

``git_diff`` used to be two steps — ``fetch_base`` then ``git_diff`` — and each
step was its own engine invocation on a fresh, shallow, ref-pinned checkout, so
the ``origin/main`` the fetch materialized was gone before the diff ran and the
diff died with ``fatal: ambiguous argument 'origin/main'`` (#957 → #965). The
step now fetches and diffs in one ``sh -c`` process (issue #972).

The test drives the workflow's *declared* argv — read back through
``load_workflow``, so it cannot drift from what ships — against a real scratch
repository. Mocking git would test nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from config import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_WORKFLOW = REPO_ROOT / ".nebuko" / "workflows" / "plan-dev-review-cmd.yaml"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _declared_argv() -> list[str]:
    definition = load_workflow(str(EXAMPLE_WORKFLOW))
    step = next(s for s in definition.steps if s.name == "git_diff")
    assert step.script is not None
    return [step.script.command, *(step.script.args or [])]


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
    _git(seed, "push", "-q", "origin", "HEAD:refs/heads/pinned")

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    _git(checkout, "remote", "add", "origin", remote.as_uri())
    _git(checkout, "fetch", "-q", "--depth=1", "origin", "pinned")
    _git(checkout, "checkout", "-q", "--detach", "FETCH_HEAD")
    return checkout


class TestGitDiffStep:
    def test_the_declared_step_fetches_the_base_ref_then_diffs(
        self, shallow_checkout: Path
    ):
        result = subprocess.run(
            _declared_argv(), cwd=shallow_checkout, capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr
        assert "+ahead line" in result.stdout

    def test_a_bare_diff_would_die_without_the_fetch(self, shallow_checkout: Path):
        """The #957 mechanism the combined step exists to prevent."""
        result = subprocess.run(
            ["git", "diff", "origin/main", "HEAD"],
            cwd=shallow_checkout,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 128
        assert "ambiguous argument" in result.stderr
