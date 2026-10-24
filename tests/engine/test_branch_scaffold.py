"""Tests for the engine-owned branch scaffold (issue #1138).

Real git against a local bare remote: the scaffold must create the feature
branch from the fetched base, rebase an existing one, return a rebase conflict
as data (fix-first note, branch restored), skip benignly and fail closed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nebuko.engine.branch_scaffold import (
    BranchScaffold,
    ScaffoldResult,
    branch_slug,
    feature_branch_name,
    issue_title_from_payload,
)

ISSUE_NUMBER = 1138
ISSUE_TITLE = "Reduce the dev prompt size"
BRANCH = "feature/issue-1138-reduce-the-dev-prompt-size"


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
    """A work checkout on ``main``, tracking a local bare remote with ``main``."""
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


def _scaffold(checkout: Path, **kwargs) -> ScaffoldResult:
    return BranchScaffold(
        checkout,
        kwargs.pop("env", {}),
        issue_number=kwargs.pop("issue_number", ISSUE_NUMBER),
        issue_title=kwargs.pop("issue_title", ISSUE_TITLE),
        **kwargs,
    ).scaffold()


class TestNaming:
    def test_slug_is_lowercase_hyphenated(self):
        assert branch_slug("Fix the Big Thing! (v2)") == "fix-the-big-thing-v2"

    def test_slug_is_capped(self):
        assert len(branch_slug("x" * 200)) <= 50

    def test_feature_branch_name(self):
        assert feature_branch_name(1234, "Reduce prompt size") == (
            "feature/issue-1234-reduce-prompt-size"
        )

    def test_issue_title_from_payload(self):
        assert issue_title_from_payload({"issue": {"title": "T"}}) == "T"
        assert issue_title_from_payload({}) == ""


class TestScaffold:
    def test_creates_the_branch_from_the_base(self, checkout):
        result = _scaffold(checkout)

        assert result.ok and not result.skipped
        assert result.branch == BRANCH
        assert _git(checkout, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH
        # The branch starts at the fetched base, not at the old HEAD.
        assert _git(checkout, "rev-parse", "HEAD") == _git(
            checkout, "rev-parse", "origin/main"
        )

    def test_rebases_an_existing_branch(self, checkout):
        _git(checkout, "checkout", "-q", "-b", BRANCH)
        _commit(checkout, "work.txt", "work\n", "work")
        # The base moves on after the branch was cut.
        seed = checkout.parent / "seed"
        _commit(seed, "base.txt", "base\nmore\n", "base moves")
        _git(seed, "push", "-q", "origin", "main")

        result = _scaffold(checkout)

        assert result.ok and not result.conflict
        assert "rebased" in result.detail
        # ``--is-ancestor`` exits non-zero (raising in ``_git``) when the base
        # is not an ancestor of the rebased HEAD.
        _git(checkout, "merge-base", "--is-ancestor", "origin/main", "HEAD")
        assert _git(checkout, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH

    def test_a_conflict_is_data_with_a_fix_first_note(self, checkout):
        _git(checkout, "checkout", "-q", "-b", BRANCH)
        _commit(checkout, "base.txt", "branch version\n", "branch edit")
        seed = checkout.parent / "seed"
        _commit(seed, "base.txt", "main version\n", "main edit")
        _git(seed, "push", "-q", "origin", "main")

        result = _scaffold(checkout)

        assert result.ok and result.conflict
        assert "FIRST" in result.note
        assert "base.txt" in result.detail
        # The rebase was aborted: the session starts on a clean branch.
        assert _git(checkout, "status", "--porcelain") == ""
        assert _git(checkout, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH

    def test_skips_without_a_remote(self, tmp_path):
        work = tmp_path / "work"
        subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)

        result = _scaffold(work)

        assert result.ok and result.skipped
        assert result.detail == "no git remote"

    def test_skips_on_a_detached_head(self, checkout):
        head = _git(checkout, "rev-parse", "HEAD")
        _git(checkout, "checkout", "-q", "--detach", head)

        result = _scaffold(checkout)

        assert result.ok and result.skipped
        assert result.detail == "detached HEAD"

    def test_skips_without_an_issue_number(self, checkout):
        result = _scaffold(checkout, issue_number=0)

        assert result.ok and result.skipped

    def test_fails_closed_when_the_base_cannot_be_fetched(self, checkout):
        # The remote exposes no ``main`` (the fixture's remote does, so point
        # the scaffold at a base that does not exist).
        result = _scaffold(checkout, env={"GITHUB_BASE_REF": "nope"})

        assert not result.ok
        assert "could not fetch origin/nope" in result.detail

    def test_the_env_overrides_the_base_branch(self, checkout):
        seed = checkout.parent / "seed"
        _git(seed, "checkout", "-q", "-b", "trunk")
        _git(seed, "push", "-q", "origin", "trunk")

        result = _scaffold(checkout, env={"GITHUB_BASE_REF": "trunk"})

        assert result.ok
        assert result.detail.endswith("from origin/trunk")
        assert _git(checkout, "rev-parse", "HEAD") == _git(
            checkout, "rev-parse", "origin/trunk"
        )

    def test_defaults_to_the_remote_head_symref(self, checkout):
        # A checkout whose ``origin/HEAD`` symref points elsewhere (a local
        # mirror of the remote's HEAD) scaffolds from that branch.
        seed = checkout.parent / "seed"
        _git(seed, "checkout", "-q", "-b", "develop")
        _git(seed, "push", "-q", "origin", "develop")
        _git(checkout, "fetch", "-q", "origin", "develop")
        _git(
            checkout,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/develop",
        )

        result = _scaffold(checkout)

        assert result.ok
        assert result.detail.endswith("from origin/develop")

    def test_defaults_to_main_without_a_remote_head_symref(self, checkout):
        # CI checkouts carry no ``origin/HEAD`` symref: the default base is
        # ``main``.
        result = _scaffold(checkout)

        assert result.ok
        assert result.detail.endswith("from origin/main")

    def test_fails_closed_outside_a_git_checkout(self, tmp_path):
        result = _scaffold(tmp_path)

        assert not result.ok
        assert "is not a git checkout" in result.detail
