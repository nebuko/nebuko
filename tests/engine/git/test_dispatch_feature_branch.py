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
