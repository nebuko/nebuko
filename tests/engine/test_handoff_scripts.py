"""The handoff cleanup / gate-check scripts (issue #965).

``handoff_cleanup.sh`` removes the document with a plain deletion commit (so it
stays recoverable from history) and is idempotent; ``handoff_check.sh`` is the
gate that fails when the document is still there.

The scripts are driven against a real scratch repository — their job is git, so
mocking git would test nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEANUP = REPO_ROOT / "scripts" / "handoff_cleanup.sh"
CHECK = REPO_ROOT / "scripts" / "handoff_check.sh"

HANDOFF_PATH = ".nebuko/handoff.md"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _run(script: Path, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script)], cwd=repo, capture_output=True, text=True
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@example.com")
    (tmp_path / ".nebuko").mkdir()
    (tmp_path / HANDOFF_PATH).write_text("# Session handoff\n\n- updated: x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add handoff")
    return tmp_path


def _write_handoff(repo: Path) -> None:
    (repo / HANDOFF_PATH).write_text("# Session handoff\n\n- updated: y\n")
    _git(repo, "add", "--", HANDOFF_PATH)
    _git(repo, "commit", "-q", "-m", "chore: update session handoff")


class TestHandoffCleanup:
    def test_removes_the_file_and_commits_the_deletion(self, repo):
        result = _run(CLEANUP, repo)

        assert result.returncode == 0
        assert not (repo / HANDOFF_PATH).exists()
        assert _git(repo, "log", "-1", "--format=%s") == "chore: remove session handoff"
        # A plain deletion commit, not a rewrite: the file stays in history.
        assert _git(repo, "log", "-1", "--diff-filter=D", "--format=%H", "--", HANDOFF_PATH)

    def test_is_idempotent_when_the_file_is_already_absent(self, repo):
        _run(CLEANUP, repo)
        head = _git(repo, "rev-parse", "HEAD")

        result = _run(CLEANUP, repo)

        assert result.returncode == 0
        # No empty commit is manufactured on the second run.
        assert _git(repo, "rev-parse", "HEAD") == head


class TestHandoffCheck:
    def test_fails_while_the_document_is_present(self, repo):
        _write_handoff(repo)

        result = _run(CHECK, repo)

        assert result.returncode != 0
        assert HANDOFF_PATH in result.stderr

    def test_passes_once_the_cleanup_ran(self, repo):
        _run(CLEANUP, repo)

        result = _run(CHECK, repo)

        assert result.returncode == 0

    def test_passes_when_no_handoff_was_ever_written(self, tmp_path):
        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)

        result = _run(CHECK, tmp_path)

        assert result.returncode == 0
