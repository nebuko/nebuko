"""Pre-push secret scan: unpublished history is redacted before it is pushed (#1347).

A credential pasted into a commit must never reach the remote. The guard scans
``<remote tip>..HEAD``, rewrites only that unpublished range and pushes the
redacted commit; published history is never touched.
"""

from __future__ import annotations

import subprocess

import pytest

from nebuko.engine.lib.git.git_session import GitSessionGuard

# Built from parts so this file never trips a scanner on itself.
FAKE_OAUTH = "gho_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _git(root, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def checkout(tmp_path, remote):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "remote", "add", "origin", str(remote))
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    # The guard is opt-in (``features.secret_scan``, issue #1347): the fixture
    # repo enables it in its initial, already-published commit.
    (path / ".nebuko").mkdir()
    (path / ".nebuko" / "nebuko.yaml").write_text(
        "features:\n  secret_scan: true\n", encoding="utf-8"
    )
    _git(path, "add", "a.txt", ".nebuko/nebuko.yaml")
    _git(path, "commit", "-m", "initial")
    _git(path, "push", "-u", "origin", "main")
    return path


def _commit(checkout, name: str, content: str, message: str = "add log") -> None:
    (checkout / name).write_text(content, encoding="utf-8")
    _git(checkout, "add", name)
    _git(checkout, "commit", "-m", message)


class TestPrePushRedaction:
    def test_the_scan_is_off_without_the_manifest_flag(self, checkout, remote):
        """``features.secret_scan`` defaults to false (issue #1347)."""
        (checkout / ".nebuko" / "nebuko.yaml").unlink()
        _commit(checkout, "console.txt", f"token={FAKE_OAUTH}\n")

        result = GitSessionGuard(checkout, {}).finalize(expected_branch="main")

        assert result.ok, result.detail
        assert result.secret_findings == ()
        pushed = subprocess.run(
            ["git", "-C", str(remote), "log", "-p", "--all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert FAKE_OAUTH in pushed

    def test_a_leaked_token_never_reaches_the_remote(self, checkout, remote):
        _commit(checkout, "console.txt", f"token={FAKE_OAUTH}\n")

        result = GitSessionGuard(checkout, {}).finalize(expected_branch="main")

        assert result.ok, result.detail
        assert [f.rule for f in result.secret_findings] == ["github-oauth"]
        pushed = subprocess.run(
            ["git", "-C", str(remote), "log", "-p", "--all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert FAKE_OAUTH not in pushed
        assert "<REDACTED:github-oauth>" in pushed

    def test_the_redaction_is_a_single_commit_on_top_of_the_remote(self, checkout, remote):
        _commit(checkout, "console.txt", f"leak {FAKE_OAUTH}\n")
        _commit(checkout, "notes.md", "second commit\n")

        result = GitSessionGuard(checkout, {}).finalize(expected_branch="main")

        assert result.ok, result.detail
        # The two unpublished commits collapse into one redacted commit on top
        # of the already-published initial one.
        assert _git(remote, "rev-list", "--count", "main") == "2"
        assert _git(remote, "rev-parse", "main") == _git(checkout, "rev-parse", "HEAD")

    def test_a_clean_session_reports_no_findings(self, checkout):
        _commit(checkout, "notes.md", "nothing secret\n")

        result = GitSessionGuard(checkout, {}).finalize(expected_branch="main")

        assert result.ok, result.detail
        assert result.secret_findings == ()

    def test_published_history_is_never_rewritten(self, checkout, remote):
        _commit(checkout, "console.txt", f"leak {FAKE_OAUTH}\n")
        _git(checkout, "push", "origin", "HEAD:refs/heads/main")
        published = _git(remote, "rev-parse", "main")

        result = GitSessionGuard(checkout, {}).finalize(expected_branch="main")

        assert result.ok, result.detail
        assert result.secret_findings == ()
        assert _git(remote, "rev-parse", "main") == published

    def test_the_detail_flags_a_source_file(self, checkout):
        _commit(checkout, "src_app.py", f"TOKEN = '{FAKE_OAUTH}'\n")

        result = GitSessionGuard(checkout, {}).finalize(expected_branch="main")

        assert result.ok, result.detail
        assert "source file" in result.detail
        assert "src_app.py" in result.detail

    def test_the_detail_does_not_flag_an_evidence_file(self, checkout):
        _commit(checkout, "console.txt", f"leak {FAKE_OAUTH}\n")

        result = GitSessionGuard(checkout, {}).finalize(expected_branch="main")

        assert result.ok, result.detail
        assert "source file" not in result.detail
