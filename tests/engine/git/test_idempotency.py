"""The git idempotency guard (issue #1194).

Real git against a scratch checkout: the ancestor check must skip an
already-merged base.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nebuko.engine.lib.git.idempotency import GitIdempotency


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _git(path, "config", "user.name", "t")
    _git(path, "config", "user.email", "t@example.com")
    _commit(path, "base.txt", "base\n", "base")
    return path


def _guard(repo: Path) -> GitIdempotency:
    def run_git(args):
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()

    return GitIdempotency(str(repo), run_git)


class TestBaseAlreadyMerged:
    def test_true_when_the_branch_contains_the_base(self, repo):
        _git(repo, "checkout", "-q", "-b", "feature/x")
        _commit(repo, "work.txt", "work\n", "work")
        # main has not moved: origin/main is an ancestor of the branch.
        _git(repo, "update-ref", "refs/remotes/origin/main", "main")

        assert _guard(repo).base_already_merged("feature/x", "main") is True

    def test_false_when_the_base_advanced(self, repo):
        _git(repo, "checkout", "-q", "-b", "feature/x")
        _commit(repo, "work.txt", "work\n", "work")
        _git(repo, "checkout", "-q", "main")
        _commit(repo, "base.txt", "base\nmore\n", "base moves")
        _git(repo, "update-ref", "refs/remotes/origin/main", "main")
        _git(repo, "checkout", "-q", "feature/x")

        assert _guard(repo).base_already_merged("feature/x", "main") is False


def _remote_only(repo: Path, branch: str) -> None:
    """Move ``branch`` to ``refs/remotes/origin/`` only (a fresh runner's view)."""
    sha = _git(repo, "rev-parse", branch)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "update-ref", f"refs/remotes/origin/{branch}", sha)
    _git(repo, "branch", "-qD", branch)


class TestRemoteOnlyBranch:
    """Issue #1268: a previous round's branch exists only on the remote."""

    @pytest.fixture()
    def remote_repo(self, repo):
        _git(repo, "update-ref", "refs/remotes/origin/main", "main")
        return repo

    def test_base_already_merged_resolves_the_remote_ref(self, remote_repo):
        _git(remote_repo, "checkout", "-q", "-b", "feature/x")
        _commit(remote_repo, "work.txt", "work\n", "work")
        _remote_only(remote_repo, "feature/x")

        assert _guard(remote_repo).base_already_merged("feature/x", "main")

    def test_a_conflict_on_a_remote_only_branch_is_reported(self, remote_repo):
        _git(remote_repo, "checkout", "-q", "-b", "feature/x")
        _commit(remote_repo, "base.txt", "branch\n", "branch side")
        _remote_only(remote_repo, "feature/x")
        _commit(remote_repo, "base.txt", "main\n", "main side")
        _git(remote_repo, "update-ref", "refs/remotes/origin/main", "main")

        assert _guard(remote_repo).has_base_conflict("feature/x", "main") == [
            "base.txt"
        ]

    def test_an_unresolvable_branch_is_a_failed_probe_not_clean(self, remote_repo):
        # ``merge-tree`` exits 1 with an empty stdout here, like a conflict:
        # it must never read as "no conflicts".
        assert _guard(remote_repo).has_base_conflict("feature/missing", "main") is None
