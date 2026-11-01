"""Red tests for the conflict-only turn-start scaffold (issue #1242).

Real git against a local bare remote (same harness as test_branch_scaffold.py):
an existing branch must be probed with a read-only merge-tree first and merged
only on a real conflict — never routinely.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nebuko.engine.lib.git.branch_scaffold import BranchScaffold

ISSUE_NUMBER = 1242
ISSUE_TITLE = "Find a better way to handle diffs in PRs"
BRANCH = "feature/issue-1242-find-a-better-way-to-handle-diffs-in-prs"


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
def checkout(tmp_path: Path) -> Path:
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())
    _commit(seed, "base.txt", "base\n", "base")
    _git(seed, "push", "-q", "origin", "main")

    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "remote", "add", "origin", remote.as_uri())
    _git(work, "fetch", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-B", "main", "origin/main")
    return work


def _scaffold(checkout: Path):
    return BranchScaffold(
        checkout,
        {},
        issue_number=ISSUE_NUMBER,
        issue_title=ISSUE_TITLE,
    ).scaffold()


class TestHasBaseConflict:
    def test_returns_the_conflicted_paths_when_the_merge_would_conflict(
        self, checkout
    ):
        # given: branch and base touched the same file since the fork point
        _git(checkout, "checkout", "-q", "-b", BRANCH)
        _commit(checkout, "base.txt", "branch\n", "branch changes base.txt")
        seed = checkout.parent / "seed"
        _git(seed, "checkout", "-q", "main")
        _commit(seed, "base.txt", "main\n", "main changes base.txt")
        _git(seed, "push", "-q", "origin", "main")

        # when: the read-only conflict probe runs
        guard = BranchScaffold(
            checkout, {}, issue_number=ISSUE_NUMBER, issue_title=ISSUE_TITLE
        )._idempotency
        conflicts = guard.has_base_conflict(BRANCH, "main")

        # then: the conflicting path is reported, nothing on disk changed
        assert conflicts == ["base.txt"]
        assert _git(checkout, "status", "--porcelain") == ""
        assert not (checkout / ".git" / "MERGE_HEAD").exists()

    def test_returns_an_empty_list_when_the_merge_is_clean(self, checkout):
        # given: main advanced with its own file, no overlap with the branch
        _git(checkout, "checkout", "-q", "-b", BRANCH)
        _commit(checkout, "work.txt", "work\n", "agent work")
        seed = checkout.parent / "seed"
        _git(seed, "checkout", "-q", "main")
        _commit(seed, "main-only.txt", "main\n", "advance main")
        _git(seed, "push", "-q", "origin", "main")

        # when: the read-only conflict probe runs
        guard = BranchScaffold(
            checkout, {}, issue_number=ISSUE_NUMBER, issue_title=ISSUE_TITLE
        )._idempotency
        conflicts = guard.has_base_conflict(BRANCH, "main")

        # then: clean merge — empty list, worktree untouched
        assert conflicts == []
        assert _git(checkout, "status", "--porcelain") == ""


class TestMergeOriginConflictOnly:
    def test_an_existing_branch_is_left_as_is_when_the_probe_is_clean(
        self, checkout
    ):
        # given: an existing branch whose base advanced without conflicts
        _git(checkout, "checkout", "-q", "-b", BRANCH)
        _commit(checkout, "work.txt", "work\n", "round 1 work")
        seed = checkout.parent / "seed"
        _git(seed, "checkout", "-q", "main")
        _commit(seed, "main-only.txt", "main\n", "advance main")
        _git(seed, "push", "-q", "origin", "main")

        # when: the scaffold runs
        result = _scaffold(checkout)

        # then: no merge happened — the branch keeps a single-parent history
        assert result.ok
        assert result.already_up_to_date
        assert "no conflicts" in result.detail
        assert _git(checkout, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH
        assert not _git(checkout, "log", "--merges", "--oneline"), "no merge commit"
        # origin/main is NOT an ancestor: the branch was left alone
        try:
            _git(checkout, "merge-base", "--is-ancestor", "origin/main", "HEAD")
            ancestor = True
        except subprocess.CalledProcessError:
            ancestor = False
        assert not ancestor

    def test_a_conflicting_branch_is_merged_and_the_conflicts_handed_over(
        self, checkout
    ):
        # given: an existing branch that conflicts with an advanced base
        _git(checkout, "checkout", "-q", "-b", BRANCH)
        _commit(checkout, "base.txt", "branch\n", "branch changes base.txt")
        seed = checkout.parent / "seed"
        _git(seed, "checkout", "-q", "main")
        _commit(seed, "base.txt", "main\n", "main changes base.txt")
        _git(seed, "push", "-q", "origin", "main")

        # when: the scaffold runs
        result = _scaffold(checkout)

        # then: the merge ran (the only path where the base enters the branch)
        # and the conflict is reported as data for the agent to fix
        assert result.ok
        assert result.conflict
        assert "base.txt" in result.detail
        assert result.note, "the agent must be told which files to resolve"

    def test_a_branch_already_containing_the_base_is_reported_up_to_date(
        self, checkout
    ):
        # given: the base never moved after the branch was cut
        _git(checkout, "checkout", "-q", "-b", BRANCH)
        _commit(checkout, "work.txt", "work\n", "round 1 work")

        # when: the scaffold runs
        result = _scaffold(checkout)

        # then: benign ok, nothing to do
        assert result.ok
        assert result.already_up_to_date
