"""A dispatch run publishes to its feature branch, never the base (issue #1432).

A ``repository_dispatch`` naming an issue must publish like a comment run:
a step that changes files opens a PR from ``feature/issue-<N>-<slug>``, and a
step that changes nothing is a success with nothing published.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import nebuko.engine.lib.agent_script.features.ensure_pr.ensure_pr as ep

BRANCH = "feature/issue-1388-lane-d-dispatch-branch"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture()
def gh_calls() -> list[list[str]]:
    return []


@pytest.fixture()
def dispatch_repo(tmp_path: Path, monkeypatch, gh_calls: list[list[str]]) -> Path:
    """A checkout on the scaffolded feature branch, cut from ``origin/main``."""
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "remote", "add", "origin", remote.as_uri())
    (work / "base.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "base.txt")
    _git(work, "commit", "-q", "-m", "base")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "fetch", "-q", "origin")
    _git(work, "checkout", "-q", "-b", BRANCH, "origin/main")

    def _fake_gh(args, **kw):
        gh_calls.append(list(args))
        if args[:1] == ["api"]:
            return "main"
        if args[:2] == ["pr", "create"]:
            return "https://github.com/nebuko/nebuko/pull/1500"
        return ""

    monkeypatch.setattr(ep, "_run_gh", _fake_gh)
    monkeypatch.setenv("GFA_REPO_ROOT", str(work))
    monkeypatch.setenv("REPO_NAME", "nebuko/nebuko")
    return work


def _pr_creates(gh_calls: list[list[str]]) -> list[list[str]]:
    return [c for c in gh_calls if c[:2] == ["pr", "create"]]


def test_a_dispatch_step_with_no_changes_succeeds_with_nothing_published(
    dispatch_repo: Path, gh_calls: list[list[str]]
):
    result = ep.ensure_pr(issue_title="Lane D dispatch branch")

    assert not result.error
    assert not result.pushed
    assert result.message == "nothing to publish: no plain delta and no PR opened"
    assert _pr_creates(gh_calls) == []


def test_a_dispatch_step_with_changes_commits_to_the_feature_branch_and_opens_the_pr(
    dispatch_repo: Path,
    gh_calls: list[list[str]],
):
    base_before = _git(dispatch_repo, "rev-parse", "origin/main")
    (dispatch_repo / "doc.md").write_text("dispatched work\n", encoding="utf-8")

    result = ep.ensure_pr(issue_title="Lane D dispatch branch")

    assert not result.error, result.message
    assert result.pushed
    assert result.pr_url == "https://github.com/nebuko/nebuko/pull/1500"
    _git(dispatch_repo, "fetch", "-q", "origin")
    assert _git(dispatch_repo, "rev-parse", f"origin/{BRANCH}") == _git(
        dispatch_repo, "rev-parse", "HEAD"
    )
    assert _git(dispatch_repo, "rev-parse", "origin/main") == base_before
    [create] = _pr_creates(gh_calls)
    assert create[create.index("--head") + 1] == BRANCH
    assert create[create.index("--base") + 1] == "main"
    assert create[create.index("--repo") + 1] == "nebuko/nebuko"


PR_URL = "https://github.com/nebuko/nebuko/pull/1500"


def _stub_gh(
    monkeypatch, gh_calls: list[list[str]], *, open_url: str = "", latest_state: str = ""
) -> None:
    """``gh`` with a configurable open PR and newest-PR state for the head."""

    def _fake_gh(args, **kw):
        gh_calls.append(list(args))
        if args[:1] == ["api"]:
            return "main"
        if args[:2] == ["pr", "create"]:
            return PR_URL
        if args[:2] == ["pr", "list"]:
            return latest_state if "--state" in args else open_url
        return ""

    monkeypatch.setattr(ep, "_run_gh", _fake_gh)


def _commit_and_push_branch(repo: Path, name: str = "doc.md", text: str = "work\n") -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", "agent work")
    _git(repo, "push", "-q", "-u", "origin", BRANCH)


class TestBranchAlreadyPushed:
    """The agent pushed the branch itself: the PR must still open."""

    def test_pushed_clean_ahead_with_no_pr_opens_one_closing_the_issue(
        self, dispatch_repo: Path, monkeypatch, gh_calls: list[list[str]]
    ):
        _stub_gh(monkeypatch, gh_calls)
        _commit_and_push_branch(dispatch_repo)

        result = ep.ensure_pr(issue_title="t", issue_number=1388)

        assert not result.error, result.message
        assert not result.pushed
        assert result.pr_url == PR_URL
        assert result.message == f"PR opened: {PR_URL}"
        [create] = _pr_creates(gh_calls)
        assert create[create.index("--head") + 1] == BRANCH
        assert create[create.index("--base") + 1] == "main"
        assert "Closes #1388" in create[create.index("--body") + 1]

    def test_pushed_with_an_open_pr_does_not_open_a_duplicate(
        self, dispatch_repo: Path, monkeypatch, gh_calls: list[list[str]]
    ):
        _stub_gh(monkeypatch, gh_calls, open_url=PR_URL)
        _commit_and_push_branch(dispatch_repo)

        result = ep.ensure_pr(issue_title="t", issue_number=1388)

        assert not result.error
        assert result.pr_url == PR_URL
        assert _pr_creates(gh_calls) == []

    def test_pushed_but_not_ahead_of_base_opens_nothing_and_fails_nothing(
        self, dispatch_repo: Path, monkeypatch, gh_calls: list[list[str]]
    ):
        _stub_gh(monkeypatch, gh_calls)
        _git(dispatch_repo, "push", "-q", "-u", "origin", BRANCH)

        result = ep.ensure_pr(issue_title="t", issue_number=1388)

        assert not result.error
        assert result.message == "nothing to publish: no plain delta and no PR opened"
        assert _pr_creates(gh_calls) == []

    def test_squash_merged_branch_with_the_base_tree_opens_no_pr(
        self, dispatch_repo: Path, monkeypatch, gh_calls: list[list[str]]
    ):
        _stub_gh(monkeypatch, gh_calls)
        _commit_and_push_branch(dispatch_repo)
        # The same content lands on main as one squash commit.
        _git(dispatch_repo, "checkout", "-q", "main")
        (dispatch_repo / "doc.md").write_text("work\n", encoding="utf-8")
        _git(dispatch_repo, "add", "doc.md")
        _git(dispatch_repo, "commit", "-q", "-m", "squash")
        _git(dispatch_repo, "push", "-q", "origin", "main")
        _git(dispatch_repo, "checkout", "-q", BRANCH)

        result = ep.ensure_pr(issue_title="t", issue_number=1388)

        assert not result.error, result.message
        assert not result.pr_url
        assert "already in origin/main" in result.message
        assert _pr_creates(gh_calls) == []

    def test_a_merged_pr_for_the_branch_blocks_a_new_pr(
        self, dispatch_repo: Path, monkeypatch, gh_calls: list[list[str]]
    ):
        _stub_gh(monkeypatch, gh_calls, latest_state="MERGED")
        _commit_and_push_branch(dispatch_repo)

        result = ep.ensure_pr(issue_title="t", issue_number=1388)

        assert not result.error
        assert not result.pr_url
        assert "already merged" in result.message
        assert _pr_creates(gh_calls) == []

    def test_a_closed_pr_does_not_block_a_new_pr_for_new_commits(
        self, dispatch_repo: Path, monkeypatch, gh_calls: list[list[str]]
    ):
        _stub_gh(monkeypatch, gh_calls, latest_state="CLOSED")
        _commit_and_push_branch(dispatch_repo)

        result = ep.ensure_pr(issue_title="t", issue_number=1388)

        assert result.pr_url == PR_URL
        assert len(_pr_creates(gh_calls)) == 1

    def test_an_uninspectable_branch_is_reported_not_read_as_nothing_to_publish(
        self, dispatch_repo: Path, monkeypatch, gh_calls: list[list[str]], caplog
    ):
        def _gh_fails_on_state(args, **kw):
            gh_calls.append(list(args))
            if "--state" in args:
                raise RuntimeError("gh pr list failed (500): boom")
            return "main" if args[:1] == ["api"] else ""

        monkeypatch.setattr(ep, "_run_gh", _gh_fails_on_state)
        _commit_and_push_branch(dispatch_repo)

        result = ep.ensure_pr(issue_title="t", issue_number=1388)

        assert result.error
        assert "could not check whether branch" in result.message
        assert "boom" in result.message
        assert _pr_creates(gh_calls) == []
        assert "could not check whether" in caplog.text


def test_a_shallow_clone_of_the_pushed_branch_still_opens_the_pr(
    tmp_path: Path, monkeypatch, gh_calls: list[list[str]]
):
    """CI checks out shallow and grafted (AGENTS.md): the ancestry and tree checks must hold there."""
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "base.txt")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "checkout", "-q", "-b", BRANCH)
    _commit_and_push_branch(seed)

    work = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--no-single-branch",
         remote.as_uri(), str(work)],
        check=True,
    )
    _git(work, "checkout", "-q", BRANCH)
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@example.com")
    assert _git(work, "rev-parse", "--is-shallow-repository") == "true"

    _stub_gh(monkeypatch, gh_calls)
    monkeypatch.setenv("GFA_REPO_ROOT", str(work))
    monkeypatch.setenv("REPO_NAME", "nebuko/nebuko")

    result = ep.ensure_pr(issue_title="t", issue_number=1388)

    assert not result.error, result.message
    assert result.pr_url == PR_URL
    [create] = _pr_creates(gh_calls)
    assert create[create.index("--head") + 1] == BRANCH


class TestPrFinalizerEntry:
    """The engine's per-phase entry (``PrFinalizer``) reaches the same decision."""

    def test_finalizer_opens_the_pr_for_a_branch_the_agent_pushed(
        self, dispatch_repo: Path, monkeypatch, gh_calls: list[list[str]]
    ):
        from nebuko.engine.lib.git.pr_finalize import PrFinalizer

        _stub_gh(monkeypatch, gh_calls)
        _commit_and_push_branch(dispatch_repo)

        result = PrFinalizer(issue_title="t", issue_number=1388, repo="nebuko/nebuko").finalize()

        assert result.ok
        assert result.pr_url == PR_URL
        assert len(_pr_creates(gh_calls)) == 1
