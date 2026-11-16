"""Tests for the read-only evidence checkout (issue #1327).

Real git against a local bare remote: a script step after ``implement`` must run
on the exact pushed SHA, with enough history for ``origin/main...HEAD``, and
never create, merge or push anything. An unreachable branch is a failure, never
a silent fallback to the workflow ref.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nebuko.engine.lib.git.evidence_checkout import (
    EvidenceCheckout,
    current_checkout,
    resolve_evidence_checkout,
)

BRANCH = "feature/issue-1327-session-checkout"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def remote(tmp_path: Path) -> Path:
    """A bare remote whose ``main`` has two commits and a feature branch."""
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())
    _commit(seed, "base.txt", "base\n", "base")
    _commit(seed, "second.txt", "second\n", "second")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "checkout", "-q", "-b", BRANCH)
    _commit(seed, "work.txt", "work\n", "work")
    _git(seed, "push", "-q", "origin", BRANCH)
    # The base moves on after the branch was pushed.
    _git(seed, "checkout", "-q", "main")
    _commit(seed, "later.txt", "later\n", "later")
    _git(seed, "push", "-q", "origin", "main")
    return remote


@pytest.fixture()
def shallow_work(tmp_path: Path, remote: Path) -> Path:
    """A depth-1 checkout of ``main``: no merge base, like ``actions/checkout``."""
    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", "--branch", "main", remote.as_uri(), str(work)],
        check=True,
    )
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@example.com")
    return work


class TestSessionCheckout:
    def test_checks_out_the_pushed_sha_detached(self, shallow_work, remote):
        expected = _git(remote, "rev-parse", BRANCH)

        checkout = resolve_evidence_checkout(
            shallow_work, {}, mode="session", branch=BRANCH
        )

        assert checkout.ok, checkout.detail
        assert checkout.branch == BRANCH
        assert checkout.head_sha == expected
        assert _git(shallow_work, "rev-parse", "HEAD") == expected
        # Detached: no local branch is created for the session.
        assert _git(shallow_work, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
        assert BRANCH not in _git(shallow_work, "branch", "--list", BRANCH)

    def test_a_shallow_checkout_can_diff_against_the_base(self, shallow_work, remote):
        resolve_evidence_checkout(shallow_work, {}, mode="session", branch=BRANCH)

        diff = _git(shallow_work, "diff", "--stat", "origin/main...HEAD")

        assert "work.txt" in diff

    def test_the_base_is_not_merged(self, shallow_work, remote):
        resolve_evidence_checkout(shallow_work, {}, mode="session", branch=BRANCH)

        # The base moved on after the push; evidence must test exactly what was
        # pushed, so the newer base commit is not in the checked-out history.
        with pytest.raises(subprocess.CalledProcessError):
            _git(shallow_work, "merge-base", "--is-ancestor", "origin/main", "HEAD")

    def test_an_unreachable_branch_is_a_failure(self, shallow_work):
        checkout = resolve_evidence_checkout(
            shallow_work, {}, mode="session", branch="feature/does-not-exist"
        )

        assert not checkout.ok
        assert "unreachable" in checkout.detail
        # No silent fallback: HEAD never moved off the workflow ref.
        assert _git(shallow_work, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    def test_no_branch_yet_runs_on_the_workflow_ref(self, shallow_work):
        checkout = resolve_evidence_checkout(shallow_work, {}, mode="session", branch="")

        assert checkout.ok
        assert checkout.branch == ""
        assert checkout.head_sha == _git(shallow_work, "rev-parse", "HEAD")


class TestWorkflowCheckout:
    def test_keeps_the_checkout_as_it_stands(self, shallow_work):
        before = _git(shallow_work, "rev-parse", "HEAD")

        checkout = resolve_evidence_checkout(
            shallow_work, {}, mode="workflow", branch=BRANCH
        )

        assert checkout.ok
        assert checkout.branch == "main"
        assert checkout.head_sha == before
        assert _git(shallow_work, "rev-parse", "HEAD") == before

    def test_a_non_git_workspace_still_runs(self, tmp_path):
        checkout = resolve_evidence_checkout(tmp_path, {}, mode="workflow", branch="")

        assert checkout.ok
        assert checkout.branch == "" and checkout.head_sha == ""


class TestCurrentCheckout:
    def test_reports_the_detached_head_without_a_branch(self, shallow_work):
        _git(shallow_work, "checkout", "-q", "--detach", "HEAD")

        checkout = current_checkout(shallow_work)

        assert checkout == EvidenceCheckout(
            ok=True, branch="", head_sha=_git(shallow_work, "rev-parse", "HEAD")
        )


class TestMergeBase:
    """Merging the base into the detached head (issue #1344).

    CI tests GitHub's pull-request merge commit, so a test step that ran on the
    bare branch head reported failures ``main`` had already fixed.
    """

    def test_merges_the_base_into_the_branch_head(self, shallow_work, remote):
        branch_head = _git(remote, "rev-parse", BRANCH)
        base_head = _git(remote, "rev-parse", "main")

        checkout = resolve_evidence_checkout(
            shallow_work, {}, mode="session", branch=BRANCH, merge_base=True
        )

        assert checkout.ok, checkout.detail
        assert checkout.tested == f"merge of {branch_head} into {base_head}"
        assert checkout.head_sha != branch_head
        # The merged tree carries both sides.
        assert (shallow_work / "later.txt").is_file()
        assert (shallow_work / "work.txt").is_file()
        parents = _git(shallow_work, "rev-list", "--parents", "-n", "1", "HEAD").split()
        assert len(parents) == 3

    def test_the_merge_commit_is_never_pushed(self, shallow_work, remote):
        branch_head = _git(remote, "rev-parse", BRANCH)

        resolve_evidence_checkout(
            shallow_work, {}, mode="session", branch=BRANCH, merge_base=True
        )

        assert _git(remote, "rev-parse", BRANCH) == branch_head

    def test_without_the_flag_the_branch_head_is_tested(self, shallow_work, remote):
        branch_head = _git(remote, "rev-parse", BRANCH)

        checkout = resolve_evidence_checkout(
            shallow_work, {}, mode="session", branch=BRANCH
        )

        assert checkout.head_sha == branch_head
        assert checkout.tested == f"branch head {branch_head}"

    def test_a_conflict_keeps_the_branch_head_and_still_succeeds(
        self, tmp_path, remote
    ):
        seed = tmp_path / "seed"
        _git(seed, "fetch", "-q", "origin")
        _git(seed, "checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
        _commit(seed, "base.txt", "branch side\n", "branch edits base")
        _git(seed, "push", "-q", "origin", BRANCH)
        _git(seed, "checkout", "-q", "-B", "main", "origin/main")
        _commit(seed, "base.txt", "main side\n", "main edits base")
        _git(seed, "push", "-q", "origin", "main")
        branch_head = _git(seed, "rev-parse", BRANCH)

        work = tmp_path / "work"
        subprocess.run(
            ["git", "clone", "-q", "--depth=1", "--branch", "main", remote.as_uri(), str(work)],
            check=True,
        )
        _git(work, "config", "user.name", "t")
        _git(work, "config", "user.email", "t@example.com")

        checkout = resolve_evidence_checkout(
            work, {}, mode="session", branch=BRANCH, merge_base=True
        )

        assert checkout.ok, checkout.detail
        assert checkout.head_sha == branch_head
        assert checkout.tested == f"branch head {branch_head}"
        # The aborted merge leaves no in-progress merge behind.
        assert not (work / ".git" / "MERGE_HEAD").exists()
