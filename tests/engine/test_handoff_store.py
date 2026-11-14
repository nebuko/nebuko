"""Tests for the engine-side session-handoff store (issue #965).

The store is the part that must not go wrong: it commits on the agent's behalf,
so it has to stage *only* the handoff file (the calling agent may have unrelated
in-flight edits) and it has to be able to revive a cleaned-up handoff.

Every test runs against a throwaway repository created in ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nebuko.engine.lib.agent_script.features.handoff_tool.handoff_document import HandoffDocument
from nebuko.engine.lib.agent_script.features.handoff_tool.handoff_store import (
    HANDOFF_RELATIVE_PATH,
    HandoffStore,
)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one commit, a ``main`` branch and an ``origin`` remote."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    # ``ensure_branch`` cuts from ``origin/main`` when it exists; a local bare
    # remote makes the push path real rather than mocked.
    remote = tmp_path.parent / f"{tmp_path.name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(tmp_path, "remote", "add", "origin", str(remote))
    return tmp_path


def _document(**overrides) -> HandoffDocument:
    fields = dict(
        completed=("a",),
        state="branch main",
        verified_facts=("`pytest` → green",),
        hypotheses=("maybe cached",),
        blockers=(),
        next_steps=("next",),
        plan_changes=("first write",),
        updated_at="2026-01-02T03:04:05Z",
    )
    fields.update(overrides)
    return HandoffDocument(**fields)


class TestPathAndLoad:
    def test_path_is_the_conventional_location(self, repo):
        assert HandoffStore(str(repo)).path() == repo / HANDOFF_RELATIVE_PATH

    def test_missing_file_loads_as_empty(self, repo):
        assert HandoffStore(str(repo)).load_text() == ""

    def test_repo_root_falls_back_to_the_environment(self, repo, monkeypatch):
        monkeypatch.setenv("GFA_REPO_ROOT", str(repo))
        assert HandoffStore().path() == repo / HANDOFF_RELATIVE_PATH


class TestWriteAndCommit:
    def test_write_commits_and_pushes_the_document(self, repo):
        store = HandoffStore(str(repo))

        commit = store.write_and_commit(_document())

        assert commit == _git(repo, "rev-parse", "HEAD")
        assert "chore: update session handoff" in _git(repo, "log", "-1", "--format=%s")
        assert store.load_text().startswith("# Session handoff")

    def test_untracked_agent_edits_are_left_alone(self, repo):
        """Staging must be targeted: the agent's own changes stay uncommitted."""
        (repo / "agent_work.py").write_text("print('wip')\n")
        store = HandoffStore(str(repo))

        store.write_and_commit(_document())

        committed = _git(repo, "show", "--name-only", "--format=", "HEAD")
        assert committed.split("\n") == [HANDOFF_RELATIVE_PATH]
        assert "agent_work.py" in _git(repo, "status", "--porcelain")

    def test_second_write_overwrites_the_file(self, repo):
        store = HandoffStore(str(repo))
        store.write_and_commit(_document(verified_facts=("STALE",)))

        store.write_and_commit(_document(verified_facts=("fresh",)))

        assert "STALE" not in store.load_text()
        assert "fresh" in store.load_text()

    def test_write_on_a_base_branch_creates_and_pushes_a_feature_branch(self, repo):
        store = HandoffStore(str(repo))

        store.write_and_commit(_document())

        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        assert branch.startswith("feature/nebuko-handoff-")
        # The branch exists on the remote, so the pre-dev handoff is reachable
        # even though the run never had a dev branch.
        remote_branches = _git(repo, "ls-remote", "--heads", "origin")
        assert branch in remote_branches

    def test_write_on_a_feature_branch_reuses_it(self, repo):
        _git(repo, "checkout", "-q", "-b", "feature/issue-1-thing")
        store = HandoffStore(str(repo))

        store.write_and_commit(_document())

        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature/issue-1-thing"

    def test_identical_rewrite_is_a_no_op_commit(self, repo):
        store = HandoffStore(str(repo))
        first = store.write_and_commit(_document())

        second = store.write_and_commit(_document())

        assert second == first


class TestRestoreFromHistory:
    def test_restores_the_last_deleted_version_into_the_worktree(self, repo):
        store = HandoffStore(str(repo))
        store.write_and_commit(_document(completed=("the work",)))
        (repo / HANDOFF_RELATIVE_PATH).unlink()
        _git(repo, "rm", "-q", "--", HANDOFF_RELATIVE_PATH)
        _git(repo, "commit", "-q", "-m", "chore: remove session handoff")

        assert store.restore_from_history() is True

        restored = store.load_text()
        assert "the work" in restored
        # Restored into the worktree, not committed: the deletion commit stays
        # in history and the revived file shows up as untracked (git collapses
        # the untracked directory, so check for either form).
        status = _git(repo, "status", "--porcelain")
        assert HANDOFF_RELATIVE_PATH in status or "?? .nebuko/" in status

    def test_is_idempotent_when_the_file_is_present(self, repo):
        store = HandoffStore(str(repo))
        store.write_and_commit(_document())

        assert store.restore_from_history() is False

    def test_returns_false_when_nothing_was_ever_written(self, repo):
        assert HandoffStore(str(repo)).restore_from_history() is False
