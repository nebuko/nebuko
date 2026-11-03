"""Tests for the conflict-only publish flow (issue #1242).

The publish-time base sync (#1219) is removed: ensure_pr pushes the agent's
commits plainly and never merges the base; the turn-start scaffold owns the
conflict merge. (The publish-time conflict *comment* #1242 sketched was never
implemented, so it is not pinned here.)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import nebuko.engine.lib.agent_script.features.ensure_pr.ensure_pr as ep
from nebuko.engine.lib.agent_script.features.ensure_pr.ensure_pr import (
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
    _git(work, "checkout", "-q", "-b", "feature/issue-1242", "origin/main")
    _commit(work, "work.txt", "work\n", "agent work")
    monkeypatch.setenv("GFA_REPO_ROOT", str(work))
    return work


def _stub_gh(monkeypatch) -> list[list[str]]:
    """Stub the gh seam so no PR is opened: returns the gh argv recorded."""
    calls: list[list[str]] = []

    def _fake_gh(args, **kw):
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return ""
        if args[:1] == ["api"]:
            return "main"
        return "https://github.com/o/r/pull/1"

    monkeypatch.setattr(ep, "_run_gh", _fake_gh)
    return calls


class TestPublishNeverMerges:
    def test_a_clean_base_advance_is_not_merged_into_the_branch(
        self, publish_repo: Path, monkeypatch
    ):
        # given: main advanced on the remote with unrelated work
        seed = publish_repo.parent / "seed"
        _git(seed, "checkout", "-q", "main")
        _commit(seed, "main-only.txt", "main\n", "advance main")
        _git(seed, "push", "-q", "origin", "main")
        _stub_gh(monkeypatch)

        # when: the publish flow runs
        result = ensure_pr(issue_title="no merge")

        # then: the push succeeds and the branch carries NO merge commit
        assert not result.error, result.message
        assert result.pushed
        assert not _git(publish_repo, "log", "--merges", "--oneline"), (
            "publish must not merge the base"
        )
