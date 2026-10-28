"""The git idempotency guards (issue #1194).

Real git against a scratch checkout: the ancestor check must skip an
already-merged base, the reverse-apply probe must recognise a patch that is
already in the tree, and the forward probe must report clean/dirty
applicability.
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


class TestPatchAlreadyApplied:
    def _diff(self, repo: Path) -> str:
        return _git(repo, "diff", "HEAD")

    def test_true_when_the_patch_reverse_applies(self, repo):
        _commit(repo, "f.txt", "a\n", "one")
        diff = _git(repo, "diff", "HEAD")  # unstaged? none — stage one edit
        (repo / "f.txt").write_text("b\n", encoding="utf-8")
        diff = _git(repo, "diff")

        assert diff
        assert _guard(repo).patch_already_applied(diff) is True

    def test_false_when_the_change_is_absent(self, repo):
        _commit(repo, "f.txt", "a\n", "one")
        diff = (
            "diff --git a/f.txt b/f.txt\n"
            "--- a/f.txt\n"
            "+++ b/f.txt\n"
            "@@ -1 +1,2 @@\n"
            " a\n"
            "+b\n"
        )

        assert _guard(repo).patch_already_applied(diff) is False

    def test_false_for_an_empty_patch(self, repo):
        assert _guard(repo).patch_already_applied("") is False


class TestPatchAppliesCleanly:
    def test_true_for_a_fresh_change(self, repo):
        # Build the new-file diff with git itself so the header format is
        # exactly what a real delivery carries (hand-written /dev/null
        # headers are rejected by git apply).
        (repo / "new.txt").write_text("new\n", encoding="utf-8")
        # --no-index exits 1 when the files differ; capture without check.
        result = subprocess.run(
            ["git", "-C", str(repo), "diff", "--no-index", "--",
             "/dev/null", "new.txt"],
            capture_output=True, text=True,
        )
        diff = result.stdout.replace(f"{repo}/new.txt", "new.txt")
        (repo / "new.txt").unlink()

        assert diff
        assert _guard(repo).patch_applies_cleanly(diff) is True

    def test_false_when_it_conflicts(self, repo):
        _commit(repo, "f.txt", "a\n", "one")
        diff = (
            "diff --git a/f.txt b/f.txt\n"
            "--- a/f.txt\n"
            "+++ b/f.txt\n"
            "@@ -1 +1,2 @@\n"
            " a\n"
            "+b\n"
        )
        (repo / "f.txt").write_text("different\n", encoding="utf-8")

        assert _guard(repo).patch_applies_cleanly(diff) is False
