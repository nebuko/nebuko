"""Branch scaffold: the session workspace is PR-ready before the agent.

The ``git-pr-workflow`` skill used to teach the agent the branch mechanics
(issue #1138); the engine now owns them. These tests pin the scaffold against
real git: fetch the base only, create ``feature/issue-<n>-<slug>`` when it does
not exist, rebase an existing one, and return a rebase conflict as data instead
of failing the run.
"""

from __future__ import annotations

import subprocess

import pytest

from nebuko.engine.branch_scaffold import (
    BranchScaffold,
    ScaffoldResult,
    branch_slug,
    feature_branch_name,
    issue_title_from_payload,
)


def _git(root, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    """A bare 'github' remote the checkout pushes to."""
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def checkout(tmp_path, remote):
    """A local checkout with ``origin`` pointing at the bare remote."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "remote", "add", "origin", str(remote))
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-m", "initial")
    _git(path, "push", "-u", "origin", "main")
    return path


def _scaffold(path, env=None) -> BranchScaffold:
    return BranchScaffold(path, env or {})


# ── Naming ───────────────────────────────────────────────────────


def test_branch_slug_is_lowercase_and_dashed():
    assert branch_slug("Reduce the size of prompt for dev agent!") == (
        "reduce-the-size-of-prompt-for-dev-agent"
    )


def test_branch_slug_collapses_punctuation():
    assert branch_slug("Fix: the Thing — now") == "fix-the-thing-now"


def test_feature_branch_name_carries_number_and_slug():
    assert feature_branch_name(1138, "Some title") == "feature/issue-1138-some-title"


def test_feature_branch_name_without_a_title():
    assert feature_branch_name(7, "") == "feature/issue-7"


def test_feature_branch_name_without_a_number():
    assert feature_branch_name(0, "Some title") == "feature/some-title"


def test_issue_title_from_payload_reads_the_issue():
    assert issue_title_from_payload({"issue": {"title": "The title"}}) == "The title"


def test_issue_title_from_payload_without_a_title():
    assert issue_title_from_payload({}) == ""
    assert issue_title_from_payload({"issue": {}}) == ""


# ── Scaffold ─────────────────────────────────────────────────────


def test_scaffold_creates_the_feature_branch_from_the_base(checkout):
    result = _scaffold(checkout).scaffold(1138, "Reduce the prompt")

    assert result.ok and not result.skipped
    assert result.branch == "feature/issue-1138-reduce-the-prompt"
    assert _git(checkout, "branch", "--show-current") == result.branch
    # Created from the fetched base, not from the previous HEAD.
    assert _git(checkout, "merge-base", "HEAD", "origin/main") == _git(
        checkout, "rev-parse", "HEAD"
    )


def test_scaffold_rebases_an_existing_branch(checkout, remote):
    _git(checkout, "checkout", "-b", "feature/issue-1138-reduce-the-prompt")
    (checkout / "b.txt").write_text("b\n", encoding="utf-8")
    _git(checkout, "add", "b.txt")
    _git(checkout, "commit", "-m", "wip")
    _git(checkout, "push", "-u", "origin", "feature/issue-1138-reduce-the-prompt")
    # The base moves on after the branch was cut.
    _git(checkout, "checkout", "main")
    (checkout / "a.txt").write_text("a2\n", encoding="utf-8")
    _git(checkout, "add", "a.txt")
    _git(checkout, "commit", "-m", "base moves")
    _git(checkout, "push", "origin", "main")

    result = _scaffold(checkout).scaffold(1138, "Reduce the prompt")

    assert result.ok and not result.skipped
    assert result.branch == "feature/issue-1138-reduce-the-prompt"
    assert _git(checkout, "branch", "--show-current") == result.branch
    # ``--is-ancestor`` exits non-zero (raising in _git) when the base is not
    # an ancestor of HEAD; on success it prints nothing.
    assert _git(checkout, "merge-base", "--is-ancestor", "origin/main", "HEAD") == ""
    assert not result.conflict


def test_scaffold_returns_a_conflict_as_data(checkout, remote):
    _git(checkout, "checkout", "-b", "feature/issue-1138-conflict")
    (checkout / "a.txt").write_text("branch version\n", encoding="utf-8")
    _git(checkout, "commit", "-am", "branch edit")
    _git(checkout, "checkout", "main")
    (checkout / "a.txt").write_text("main version\n", encoding="utf-8")
    _git(checkout, "commit", "-am", "main edit")
    _git(checkout, "push", "origin", "main")

    result = _scaffold(checkout).scaffold(1138, "conflict")

    # Not fatal: the run proceeds and the prompt carries the fix-first note.
    assert result.ok and not result.skipped
    assert result.branch == "feature/issue-1138-conflict"
    assert "rebase" in result.conflict.lower()
    assert "FIRST" in result.conflict
    assert "a.txt" in result.conflict


def test_scaffold_skips_without_a_remote(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)

    result = _scaffold(path).scaffold(1, "t")

    assert result.ok and result.skipped
    assert result.branch is None
    assert "no git remote" in result.detail


def test_scaffold_skips_on_a_detached_head(checkout):
    sha = _git(checkout, "rev-parse", "HEAD")
    _git(checkout, "checkout", sha)

    result = _scaffold(checkout).scaffold(1, "t")

    assert result.ok and result.skipped
    assert result.branch is None


def test_scaffold_fails_closed_when_the_fetch_fails(checkout):
    result = BranchScaffold(checkout, {}, base="missing-base").scaffold(1, "t")

    assert not result.ok
    assert "could not fetch" in result.detail


def test_scaffold_honours_the_base_branch_env(checkout, remote):
    _git(checkout, "checkout", "-b", "trunk")
    (checkout / "trunk.txt").write_text("t\n", encoding="utf-8")
    _git(checkout, "add", "trunk.txt")
    _git(checkout, "commit", "-m", "trunk")
    _git(checkout, "push", "origin", "trunk")

    result = BranchScaffold(checkout, {"BASE_BRANCH": "trunk"}).scaffold(1, "t")

    assert result.ok
    assert _git(checkout, "merge-base", "HEAD", "origin/trunk") == _git(
        checkout, "rev-parse", "HEAD"
    )
