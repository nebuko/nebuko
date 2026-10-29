"""Tests for the publish-time base sync in ``ensure_pr`` (issue #1219).

Real git against a local bare remote (same harness style as
``test_branch_scaffold.py``): the publisher must merge a freshly advanced
``origin/<base>`` into the branch before pushing, skip the merge when the
base is already contained, and refuse the push (fail-closed) when the fetch
fails or the merge conflicts — reporting the refusal through the existing
``EnsurePrResult.error`` path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from agent_script.features.ensure_pr.ensure_pr import (
    EnsurePrResult,
    _ensure_pr_impl,
    ensure_pr,
)


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
def publish_repo(tmp_path: Path, monkeypatch) -> Path:
    """A work checkout on feature branch, tracking a bare remote with main.

    The branch carries one committed, unpushed change so the publish flow
    has something to push; ``GFA_REPO_ROOT`` pins ensure_pr's git seam to
    this checkout.
    """
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
    _git(work, "checkout", "-q", "-b", "feature/issue-1219", "origin/main")
    _commit(work, "work.txt", "work\n", "agent work")
    monkeypatch.setenv("GFA_REPO_ROOT", str(work))
    return work


def _stub_gh(monkeypatch) -> list[list[str]]:
    """Stub the gh seam so no PR is opened: returns the gh argv recorded."""
    import agent_script.features.ensure_pr.ensure_pr as ep

    calls: list[list[str]] = []
    def _fake_gh(args, **kw):
        calls.append(args)
        # pr list -> none open; pr create -> a fake URL so create succeeds.
        if args[:2] == ["pr", "list"]:
            return ""
        if args[:1] == ["api"]:
            return "main"
        return "https://github.com/o/r/pull/1"

    monkeypatch.setattr(ep, "_run_gh", _fake_gh)
    return calls


def test_publish_merges_a_freshly_advanced_base_before_pushing(
    publish_repo: Path, monkeypatch
) -> None:
    # given: main advanced on the remote after the branch was cut
    seed = publish_repo.parent / "seed"
    _git(seed, "checkout", "-q", "main")
    _commit(seed, "main-only.txt", "main\n", "advance main")
    _git(seed, "push", "-q", "origin", "main")
    _stub_gh(monkeypatch)

    # when: the publish flow runs
    result = ensure_pr(issue_title="sync")

    # then: the push succeeds and origin/main is an ancestor of the branch
    assert not result.error, result.message
    assert result.pushed
    remote_head = _git(publish_repo, "rev-parse", "origin/main")
    assert _git(publish_repo, "merge-base", "--is-ancestor", remote_head, "HEAD") == ""


def test_publish_skips_the_merge_when_the_base_is_already_contained(
    publish_repo: Path, monkeypatch
) -> None:
    # given: main has not moved since the branch was cut from it
    calls = _stub_gh(monkeypatch)

    # when: the publish flow runs
    result = ensure_pr(issue_title="sync")

    # then: the push succeeds without a merge commit (main already contained)
    assert not result.error, result.message
    assert result.pushed
    assert not _git(publish_repo, "log", "--merges", "--oneline"), "no merge commit"


def test_publish_refuses_to_push_when_the_base_fetch_fails(
    publish_repo: Path, monkeypatch
) -> None:
    # given: fetching origin fails (broken remote URL)
    import agent_script.features.ensure_pr.ensure_pr as ep

    real = ep._run_git

    def _fail_fetch(args, **kw):
        if args[:1] == ["fetch"]:
            raise RuntimeError("git fetch failed: connection refused")
        return real(args, **kw)

    monkeypatch.setattr(ep, "_run_git", _fail_fetch)
    calls = _stub_gh(monkeypatch)

    # when: the publish flow runs
    result = ensure_pr(issue_title="sync")

    # then: the publish is refused as an error, nothing pushed, no PR opened
    assert result.error, result.message
    assert not result.pushed
    assert "fetch" in result.message.lower()
    assert not any(a[:1] == ["push"] for a in calls)


def test_publish_refuses_to_push_on_a_merge_conflict(
    publish_repo: Path, monkeypatch
) -> None:
    # given: main changed the same file the branch changed
    seed = publish_repo.parent / "seed"
    _git(seed, "checkout", "-q", "main")
    _commit(seed, "base.txt", "main\n", "conflicting main")
    _git(seed, "push", "-q", "origin", "main")
    calls = _stub_gh(monkeypatch)

    # when: the publish flow runs
    result = ensure_pr(issue_title="sync")

    # then: the merge is aborted, the publish refused, no push and no PR call
    assert result.error, result.message
    assert not result.pushed
    assert "conflict" in result.message.lower()
    assert not any(a[:1] == ["push"] for a in calls)
    assert not any(a[:2] == ["pr", "list"] for a in calls)
    # the merge left nothing in progress
    assert not (publish_repo / ".git" / "MERGE_HEAD").exists()


def test_publish_pushes_the_sync_merge_commit_together_with_the_branch(
    publish_repo: Path, monkeypatch
) -> None:
    # given: main advanced on the remote
    seed = publish_repo.parent / "seed"
    _git(seed, "checkout", "-q", "main")
    _commit(seed, "main-only.txt", "main\n", "advance main")
    _git(seed, "push", "-q", "origin", "main")
    _stub_gh(monkeypatch)

    # when: the publish flow runs
    result = ensure_pr(issue_title="sync")

    # then: the remote branch is the merged head (merge commit pushed along)
    assert not result.error, result.message
    assert result.pushed
    assert _git(publish_repo, "log", "--merges", "--oneline"), "merge commit exists"
    remote_branch = _git(publish_repo, "rev-parse", "origin/feature/issue-1219")
    assert remote_branch == _git(publish_repo, "rev-parse", "HEAD")
