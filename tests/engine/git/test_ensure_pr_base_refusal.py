"""ensure_pr on the wrong branch, and its failure detail (issue #1268).

A session that ended on the base branch must be refused before anything is
committed or pushed, and an exception with an empty message must still log a
diagnosable detail (the run log showed a bare ``ensure_pr failed: ``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import nebuko.engine.lib.agent_script.features.ensure_pr.ensure_pr as ep


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture()
def on_main(tmp_path: Path, monkeypatch) -> Path:
    """A checkout on ``main`` with a local commit the agent made there."""
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
    (work / "doc.md").write_text("agent work\n", encoding="utf-8")
    _git(work, "add", "doc.md")
    _git(work, "commit", "-q", "-m", "agent work on main")
    monkeypatch.setenv("GFA_REPO_ROOT", str(work))

    def _fake_gh(args, **kw):
        if args[:1] == ["api"]:
            return "main"
        return ""

    monkeypatch.setattr(ep, "_run_gh", _fake_gh)
    return work


def test_publishing_from_the_base_branch_is_refused(on_main: Path):
    remote_main = _git(on_main, "rev-parse", "origin/main")

    result = ep.ensure_pr(issue_title="t")

    assert result.error
    assert "refusing to publish from the base branch 'main'" in result.message
    assert not result.pushed
    # Nothing reached the remote base.
    _git(on_main, "fetch", "-q", "origin")
    assert _git(on_main, "rev-parse", "origin/main") == remote_main


class TestFormatFailureDetail:
    def test_an_empty_message_still_names_the_exception(self):
        assert ep.format_failure_detail(NotImplementedError()) == "NotImplementedError"

    def test_the_message_follows_the_type(self):
        assert ep.format_failure_detail(RuntimeError("boom")) == "RuntimeError: boom"


def test_an_empty_exception_never_yields_a_bare_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(ep, "_ensure_pr_impl", _boom)

    result = ep.ensure_pr()

    assert result.error
    assert result.message == "error: NotImplementedError"
