"""Red tests for the engine's dirty-tree auto-commit message (issue #1153).

The engine's forgotten-changes commit must be recognizable as an engine-made
commit: message ``chore: engine: commit changes forgotten by agent`` and the
short sha handed back so it can be recorded in the state's git history.
"""

from __future__ import annotations

import subprocess

import pytest

from nebuko.engine.lib.git.git_session import GitSessionGuard


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-m", "initial")
    return path


class TestDirtyTreeAutoCommit:
    def test_commit_message_is_the_engine_marker(self, checkout):
        (checkout / "left.txt").write_text("left\n", encoding="utf-8")

        guard = GitSessionGuard(checkout, {})
        guard._commit_all_pending()

        assert "chore: engine: commit changes forgotten by agent" in _git(
            checkout, "log", "-1", "--pretty=%B"
        )

    def test_returns_the_short_sha_of_the_commit_made(self, checkout):
        (checkout / "left.txt").write_text("left\n", encoding="utf-8")

        sha = GitSessionGuard(checkout, {})._commit_all_pending()

        assert sha == _git(checkout, "rev-parse", "--short", "HEAD")

    def test_a_clean_tree_returns_an_empty_string(self, checkout):
        assert GitSessionGuard(checkout, {})._commit_all_pending() == ""

    def test_workflow_files_are_never_committed(self, checkout):
        workflows = checkout / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yaml").write_text("on: [push]\n", encoding="utf-8")
        (checkout / "left.txt").write_text("left\n", encoding="utf-8")

        sha = GitSessionGuard(checkout, {})._commit_all_pending()

        assert sha
        assert _git(checkout, "show", "--name-only", "--format=") == "left.txt"
        # The workflow file stays in the tree for the patch delivery.
        assert (workflows / "ci.yaml").exists()

    def test_workflow_only_leftovers_make_no_commit(self, checkout):
        workflows = checkout / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yaml").write_text("on: [push]\n", encoding="utf-8")

        assert GitSessionGuard(checkout, {})._commit_all_pending() == ""
        assert _git(checkout, "rev-list", "--count", "HEAD") == "1"

    def test_unresolved_conflicts_are_never_committed(self, checkout):
        _git(checkout, "checkout", "-q", "-b", "side")
        (checkout / "a.txt").write_text("side\n", encoding="utf-8")
        _git(checkout, "commit", "-qam", "side")
        _git(checkout, "checkout", "-q", "main")
        (checkout / "a.txt").write_text("main\n", encoding="utf-8")
        _git(checkout, "commit", "-qam", "main")
        subprocess.run(["git", "-C", str(checkout), "merge", "side"], capture_output=True)

        with pytest.raises(RuntimeError, match="unresolved merge conflicts.*a.txt"):
            GitSessionGuard(checkout, {})._commit_all_pending()
