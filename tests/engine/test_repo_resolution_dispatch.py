"""Dispatch runs target the repo they were aimed at, never the caller's (issue #1388)."""

from __future__ import annotations

import subprocess

import pytest

from nebuko.engine.lib.event_normalizer import normalize_trigger
from nebuko.engine.lib.git.git_session import GitSessionGuard
from nebuko.engine.lib.repo_resolution import resolve_repo
from nebuko.engine.models import Trigger


def _dispatch_trigger() -> Trigger:
    # The caller repo (olivier-norture/nebuko-claude-code) is the event's repository.
    return normalize_trigger(
        {
            "repository": {"full_name": "olivier-norture/nebuko-claude-code"},
            "client_payload": {
                "actor": "alice",
                "org_repo": "nebuko/nebuko",
                "issue_number": 1388,
                "prompt_body": "@@dev go",
            },
        }
    )


class TestResolveRepo:
    def test_env_wins_over_dispatch_payload_repository(self):
        env = {"REPO_NAME": "nebuko/nebuko", "GITHUB_REPOSITORY": "olivier-norture/nebuko-claude-code"}

        assert resolve_repo(env, _dispatch_trigger()) == "nebuko/nebuko"

    def test_github_repository_used_when_repo_name_unset(self):
        env = {"GITHUB_REPOSITORY": "nebuko/nebuko"}

        assert resolve_repo(env, _dispatch_trigger()) == "nebuko/nebuko"

    def test_org_repo_used_when_env_is_empty(self):
        assert resolve_repo({}, _dispatch_trigger()) == "nebuko/nebuko"

    def test_issue_trigger_falls_back_to_payload_repository(self):
        trigger = Trigger(
            author="alice",
            body="",
            type="issue",
            number=7,
            payload={"repository": {"full_name": "acme/app"}},
        )

        assert resolve_repo({}, trigger) == "acme/app"

    def test_undeterminable_repo_is_empty(self):
        assert resolve_repo({}, None) == ""


def test_normalizer_keeps_org_repo_on_the_trigger():
    assert _dispatch_trigger().org_repo == "nebuko/nebuko"


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def checkout_with_remote(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-m", "initial")
    _git(path, "remote", "add", "origin", str(remote))
    return path, remote


def test_finalize_refuses_push_of_base_branch(checkout_with_remote):
    path, remote = checkout_with_remote

    result = GitSessionGuard(path, {}).finalize()

    assert not result.ok
    assert "base branch" in result.detail
    assert _git(remote, "branch", "--list", "main") == ""


class _Client:
    def __init__(self, issue=None, error: Exception | None = None):
        self._issue = issue
        self._error = error
        self.issue_reads: list[tuple[str, int]] = []

    def get_issue(self, repo, number):
        self.issue_reads.append((repo, number))
        if self._error is not None:
            raise self._error
        return self._issue

    def get_issue_comments(self, repo, number, limit=None):
        return []


def _adapter_for(client, monkeypatch, env_repo="nebuko/nebuko"):
    from nebuko.adapters.github import GitHubAdapter

    monkeypatch.setenv("GITHUB_REPOSITORY", "olivier-norture/nebuko-claude-code")
    monkeypatch.setenv("REPO_NAME", env_repo)
    adapter = GitHubAdapter(client=client)
    adapter._trigger = _dispatch_trigger()
    return adapter


def test_adapter_reads_the_target_repo_on_dispatch(monkeypatch):
    client = _Client(issue={"number": 1388})

    _adapter_for(client, monkeypatch).load_raw_thread()

    assert client.issue_reads == [("nebuko/nebuko", 1388)]


def test_unreadable_target_issue_raises(monkeypatch):
    from nebuko.adapters.github import TargetIssueUnreadable

    client = _Client(error=RuntimeError("HTTP 404"))

    with pytest.raises(TargetIssueUnreadable):
        _adapter_for(client, monkeypatch).load_raw_thread()


class TestMainTargetsTheDispatchRepo:
    """Issue #1388 follow-up: ``main()`` must hand the adapter the org repo."""

    @pytest.fixture
    def adapter_kwargs(self, monkeypatch, mocker):
        import nebuko.adapters as adapters
        import nebuko.engine.boot as boot
        import nebuko.engine.run_loop as run_loop

        captured: dict = {}

        def fake_build(kind, **kwargs):
            captured["kind"] = kind
            captured.update(kwargs)
            return mocker.Mock()

        monkeypatch.setattr(adapters, "build_adapter", fake_build)
        monkeypatch.setattr(boot, "prewarm_agent_sdk", lambda env: None)
        mocker.patch.object(
            run_loop.Engine, "run", autospec=True, return_value=mocker.Mock(exit_code=0)
        )
        return captured

    def _run(self, monkeypatch, env, argv=()):
        import nebuko.engine.run_loop as run_loop

        monkeypatch.delenv("REPO_NAME", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return run_loop.main(["--adapter", "github", *argv])

    def test_dispatch_env_targets_the_org_repo_not_the_caller(self, monkeypatch, adapter_kwargs):
        self._run(
            monkeypatch,
            {"GITHUB_REPOSITORY": "olivier-norture/nebuko-claude-code", "REPO_NAME": "nebuko/nebuko"},
        )

        assert adapter_kwargs["repo"] == "nebuko/nebuko"

    def test_explicit_repo_flag_wins(self, monkeypatch, adapter_kwargs):
        self._run(
            monkeypatch,
            {"GITHUB_REPOSITORY": "olivier-norture/nebuko-claude-code", "REPO_NAME": "nebuko/nebuko"},
            argv=("--repo", "someone/else"),
        )

        assert adapter_kwargs["repo"] == "someone/else"

    def test_github_repository_is_the_default_without_repo_name(self, monkeypatch, adapter_kwargs):
        self._run(monkeypatch, {"GITHUB_REPOSITORY": "nebuko/nebuko"})

        assert adapter_kwargs["repo"] == "nebuko/nebuko"

    def test_resolved_repo_is_logged_once(self, monkeypatch, adapter_kwargs, caplog):
        caplog.set_level("INFO", logger="nebuko.engine.run_loop")

        self._run(
            monkeypatch,
            {"GITHUB_REPOSITORY": "olivier-norture/nebuko-claude-code", "REPO_NAME": "nebuko/nebuko"},
        )

        markers = [r.getMessage() for r in caplog.records if "[phase:repo]" in r.getMessage()]
        assert len(markers) == 1
        assert "target repo nebuko/nebuko" in markers[0]
