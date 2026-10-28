"""Red tests for the git idempotency guards (issue #1194).

Real git against local repos: ``GitIdempotency`` must detect an
already-merged base (ancestor check), a patch already present in the tree
(reverse apply check), and clean forward applicability.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from nebuko.engine.lib.git.idempotency import GitIdempotency


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", message)


def _run_git(repo: Path) -> Callable[[Sequence[str]], str]:
    def run(args: Sequence[str]) -> str:
        return _git(repo, *args, check=False)

    return run


NEW_FILE_PATCH = (
    "diff --git a/f.txt b/f.txt\n"
    "new file mode 100644\n"
    "index 0000000..ce01362\n"
    "--- /dev/null\n"
    "+++ b/f.txt\n"
    "@@ -0,0 +1 @@\n"
    "+hello\n"
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@example.com")
    _commit(work, "base.txt", "base\n", "base")
    return work


class TestBaseAlreadyMerged:
    def test_true_when_branch_contains_base(self, repo: Path):
        _git(repo, "branch", "feature")
        guard = GitIdempotency(str(repo), _run_git(repo))
        assert guard.base_already_merged("feature", "main") is True

    def test_false_when_branch_behind_base(self, repo: Path):
        _git(repo, "checkout", "-q", "-b", "behind", "HEAD")
        _commit(repo, "new.txt", "new\n", "advance base")
        _git(repo, "checkout", "-q", "main")
        guard = GitIdempotency(str(repo), _run_git(repo))
        assert guard.base_already_merged("behind", "main") is False


class TestPatchAlreadyApplied:
    def test_true_when_patch_reverse_applies(self, repo: Path):
        (repo / "f.txt").write_text("hello\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        guard = GitIdempotency(str(repo), _run_git(repo))
        assert guard.patch_already_applied(NEW_FILE_PATCH) is True

    def test_false_when_patch_not_in_tree(self, repo: Path):
        guard = GitIdempotency(str(repo), _run_git(repo))
        assert guard.patch_already_applied(NEW_FILE_PATCH) is False


class TestPatchAppliesCleanly:
    def test_true_for_clean_patch(self, repo: Path):
        guard = GitIdempotency(str(repo), _run_git(repo))
        assert guard.patch_applies_cleanly(NEW_FILE_PATCH) is True

    def test_false_for_conflicting_patch(self, repo: Path):
        (repo / "f.txt").write_text("different\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        guard = GitIdempotency(str(repo), _run_git(repo))
        assert guard.patch_applies_cleanly(NEW_FILE_PATCH) is False
