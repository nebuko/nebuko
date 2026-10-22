"""Git session guard: fail closed on credentials and on leftover work.

A credentials-less run used to finish green while every ``git push`` failed
(run 39625740695), and nothing guaranteed a session ended fully committed and
pushed. ``GitSessionGuard`` proves auth before the agent and the published tree
after it (issue #1089); these tests pin both entry points against real git.
"""

from __future__ import annotations

import subprocess

import pytest

from nebuko.engine.git_session import (
    BENIGN_SKIPS,
    GIT_IDENTITY_EMAIL,
    GIT_IDENTITY_NAME,
    GitSessionGuard,
    GuardResult,
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


def _local_guard(path, env=None) -> GitSessionGuard:
    """A guard whose ``origin`` is a local path (never github/HTTPS)."""
    return GitSessionGuard(path, env or {})


# ── preflight ────────────────────────────────────────────────────


def test_preflight_passes_on_a_reachable_remote(checkout, remote):
    result = _local_guard(checkout).preflight()

    assert result.ok and not result.skipped
    # A working auth needs no token installed.
    config = _git(checkout, "config", "--local", "--list")
    assert "extraheader" not in config


def test_preflight_writes_the_repo_local_identity(checkout):
    result = _local_guard(checkout).preflight()

    assert result.ok
    assert _git(checkout, "config", "--local", "user.name") == GIT_IDENTITY_NAME
    assert _git(checkout, "config", "--local", "user.email") == GIT_IDENTITY_EMAIL


def test_preflight_skips_without_a_remote(tmp_path):
    path = tmp_path / "norepo"
    path.mkdir()
    _git(path, "init", "-b", "main")

    result = _local_guard(path).preflight()

    assert result.ok and result.skipped
    assert result.detail in BENIGN_SKIPS


def test_preflight_fails_on_a_non_git_directory(tmp_path):
    path = tmp_path / "plain"
    path.mkdir()

    result = _local_guard(path).preflight()

    assert not result.ok and not result.skipped
    assert "not a git checkout" in result.detail


def test_preflight_refuses_a_token_for_a_non_github_remote(checkout, tmp_path):
    """A token extraheader cannot authenticate an arbitrary host."""
    broken = tmp_path / "missing.git"
    _git(checkout, "remote", "set-url", "origin", str(broken))

    result = _local_guard(checkout, {"NEBUKO_GIT_TOKEN": "secret"}).preflight()

    assert not result.ok
    assert "cannot authenticate" in result.detail
    # The token is still installed only for github HTTPS; nothing leaked.
    assert "secret" not in result.detail


def test_preflight_reports_the_env_var_to_set_without_a_token(checkout):
    guard = _local_guard(checkout)
    guard._remote_url = lambda: "https://github.com/acme/repo.git"  # type: ignore[method-assign]
    guard._verify_remote = lambda: "authentication failed"  # type: ignore[method-assign]

    result = guard.preflight()

    assert not result.ok
    assert "NEBUKO_GIT_TOKEN" in result.detail
    assert "https://github.com/acme/repo.git" in result.detail


def test_preflight_names_the_remote_for_a_non_github_failure(checkout):
    guard = _local_guard(checkout)
    guard._verify_remote = lambda: "host unreachable"  # type: ignore[method-assign]

    result = guard.preflight()

    assert not result.ok
    assert "configure credentials for" in result.detail
    assert "host unreachable" in result.detail


def test_preflight_installs_the_token_and_reprobes(checkout):
    installed: list[list[str]] = []
    guard = GitSessionGuard(checkout, {"NEBUKO_GIT_TOKEN": "tok-123"})
    probes = iter(["boom", None])

    guard._configure_identity = lambda: None  # type: ignore[method-assign]
    guard._verify_remote = lambda: next(probes)  # type: ignore[method-assign]
    guard._is_token_remote = lambda url: True  # type: ignore[method-assign]
    guard._install_credentials = lambda: installed.append(["token"]) or True  # type: ignore[method-assign]

    result = guard.preflight()

    assert result.ok
    assert installed == [["token"]]


def test_install_credentials_encodes_the_token(checkout):
    guard = GitSessionGuard(checkout, {"NEBUKO_GIT_TOKEN": "tok-123"})

    assert guard._install_credentials() is True
    header = _git(
        checkout, "config", "--local", "http.https://github.com/.extraheader"
    )
    assert header.startswith("AUTHORIZATION: basic ")
    assert "tok-123" not in header


def test_token_precedence_prefers_the_dedicated_variable(checkout):
    guard = GitSessionGuard(
        checkout, {"GH_TOKEN": "gh", "NEBUKO_GIT_TOKEN": "dedicated"}
    )

    assert guard._resolve_token() == "dedicated"
    assert GitSessionGuard(checkout, {"GITHUB_TOKEN": "g"})._resolve_token() == "g"
    assert GitSessionGuard(checkout, {})._resolve_token() is None


# ── finalize ─────────────────────────────────────────────────────


def test_finalize_passes_a_clean_pushed_tree(checkout):
    result = _local_guard(checkout).finalize()

    assert result.ok and not result.skipped


def test_finalize_commits_and_pushes_leftovers(checkout, remote):
    (checkout / "left.txt").write_text("left\n", encoding="utf-8")
    _git(checkout, "checkout", "-b", "feature/x")

    result = _local_guard(checkout).finalize()

    assert result.ok, result.detail
    assert _git(checkout, "status", "--porcelain") == ""
    assert _git(checkout, "rev-parse", "HEAD") == _git(
        remote, "rev-parse", "feature/x"
    )
    author = _git(checkout, "log", "-1", "--format=%an <%ae>")
    assert author == f"{GIT_IDENTITY_NAME} <{GIT_IDENTITY_EMAIL}>"


def test_finalize_respects_gitignore(checkout, remote):
    (checkout / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(checkout, "add", ".gitignore")
    _git(checkout, "commit", "-m", "ignore logs")
    _git(checkout, "push", "origin", "main")
    (checkout / "debug.log").write_text("noise\n", encoding="utf-8")
    (checkout / "kept.txt").write_text("keep\n", encoding="utf-8")

    result = _local_guard(checkout).finalize()

    assert result.ok, result.detail
    assert (checkout / "debug.log").exists()
    tracked = _git(checkout, "ls-files")
    assert "kept.txt" in tracked and "debug.log" not in tracked


def test_finalize_fails_when_the_push_is_rejected(checkout, remote):
    _git(checkout, "checkout", "-b", "feature/y")
    # Advance the remote branch out of band: a non-fast-forward push fails.
    other = checkout.parent / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.name", "test")
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "checkout", "-b", "feature/y")
    (other / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    _git(other, "add", "theirs.txt")
    _git(other, "commit", "-m", "theirs")
    _git(other, "push", "origin", "feature/y")
    (checkout / "mine.txt").write_text("mine\n", encoding="utf-8")

    result = _local_guard(checkout).finalize()

    assert not result.ok
    assert "could not push feature/y" in result.detail


def test_finalize_skips_without_a_remote(tmp_path):
    path = tmp_path / "norepo"
    path.mkdir()
    _git(path, "init", "-b", "main")

    result = _local_guard(path).finalize()

    assert result.ok and result.skipped


def test_finalize_never_raises_outside_a_checkout(tmp_path):
    path = tmp_path / "plain"
    path.mkdir()

    result = _local_guard(path).finalize()

    assert result.ok and result.skipped


def test_finalize_keeps_the_token_out_of_the_detail(checkout):
    guard = GitSessionGuard(checkout, {"NEBUKO_GIT_TOKEN": "tok-secret"})
    guard._push_branch = lambda branch: "could not push: rejected"  # type: ignore[method-assign]
    (checkout / "left.txt").write_text("left\n", encoding="utf-8")

    result = guard.finalize()

    assert not result.ok
    assert "tok-secret" not in result.detail


def test_guard_result_defaults_are_a_clean_pass():
    result = GuardResult(ok=True)

    assert not result.skipped and result.detail == ""
