"""Executor tests for the deterministic delete-comments command (issue #644).

Subprocess layer only is mocked (``delete_comments._run``); the real
listing/deletion logic is exercised. Covers the spec for the pure deletion
action (issue #674): the command deletes every comment through the GitHub API
and does nothing else — no result object, no comment posting.
"""

import os

import pytest

from features.delete_comments import delete_comments as dc


def _proc(returncode: int, stdout: str = "", stderr: str = ""):
    import subprocess

    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _fake_run(script):
    """Return a ``_run`` replacement replaying canned per-call responses.

    ``script`` maps a substring of the joined args to a (returncode, stdout,
    stderr) response. Listing calls contain ``--paginate``; delete calls
    contain ``--method DELETE`` + the comment id.
    """
    calls = []

    def fake_run(args, token=None):
        calls.append(list(args))
        joined = " ".join(args)
        for key, response in script.items():
            if key in joined:
                rc, out, err = response
                return _proc(rc, out, err)
        return _proc(0, "", "")

    return fake_run, calls


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "test-token")


# ── list_comment_ids ─────────────────────────────────────────────────


class TestListCommentIds:
    def test_parses_numeric_ids_ascending(self, monkeypatch):
        fake, _ = _fake_run({"--paginate": (0, "300\n100\n200\n", "")})
        monkeypatch.setattr(dc, "_run", fake)
        assert dc.list_comment_ids("o/r", 1) == [100, 200, 300]

    def test_ignores_non_numeric_lines(self, monkeypatch):
        fake, _ = _fake_run({"--paginate": (0, "100\n\n200\n", "")})
        monkeypatch.setattr(dc, "_run", fake)
        assert dc.list_comment_ids("o/r", 1) == [100, 200]

    def test_ignores_non_ascii_digit_lines(self, monkeypatch):
        # '²' passes str.isdigit() but int() rejects it; such lines must be
        # dropped rather than crashing (issue #644 review).
        fake, _ = _fake_run({"--paginate": (0, "100\n²\n200\n", "")})
        monkeypatch.setattr(dc, "_run", fake)
        assert dc.list_comment_ids("o/r", 1) == [100, 200]

    def test_nonzero_exit_raises(self, monkeypatch):
        fake, _ = _fake_run({"--paginate": (1, "", "HTTP 403: bad creds")})
        monkeypatch.setattr(dc, "_run", fake)
        with pytest.raises(RuntimeError, match="failed to list"):
            dc.list_comment_ids("o/r", 1)

    def test_empty_issue(self, monkeypatch):
        fake, _ = _fake_run({"--paginate": (0, "", "")})
        monkeypatch.setattr(dc, "_run", fake)
        assert dc.list_comment_ids("o/r", 1) == []


# ── read_config ──────────────────────────────────────────────────────


class TestReadConfig:
    def test_defaults_repo_to_github_repository(self):
        cfg = dc.read_config(
            {"GITHUB_REPOSITORY": "o/r", "DELETE_COMMENTS_ISSUE": "7"}
        )
        assert cfg["repo"] == "o/r"
        assert cfg["issue"] == 7

    def test_explicit_repo_wins(self):
        cfg = dc.read_config(
            {
                "DELETE_COMMENTS_REPO": "a/b",
                "GITHUB_REPOSITORY": "o/r",
                "DELETE_COMMENTS_ISSUE": "7",
            }
        )
        assert cfg["repo"] == "a/b"

    def test_missing_repo_raises(self):
        with pytest.raises(ValueError, match="DELETE_COMMENTS_REPO"):
            dc.read_config({"DELETE_COMMENTS_ISSUE": "7"})

    def test_missing_issue_raises(self):
        with pytest.raises(ValueError, match="DELETE_COMMENTS_ISSUE"):
            dc.read_config({"DELETE_COMMENTS_REPO": "o/r"})

    def test_non_numeric_issue_raises(self):
        with pytest.raises(ValueError, match="DELETE_COMMENTS_ISSUE"):
            dc.read_config({"DELETE_COMMENTS_REPO": "o/r", "DELETE_COMMENTS_ISSUE": "abc"})

    def test_non_ascii_digit_issue_raises(self):
        # '²' passes str.isdigit() but int() rejects it; must raise, not crash.
        with pytest.raises(ValueError, match="DELETE_COMMENTS_ISSUE"):
            dc.read_config({"DELETE_COMMENTS_REPO": "o/r", "DELETE_COMMENTS_ISSUE": "²"})


# ── delete_issue_comments — pure action contract (issue #674) ────────
#
# Spec for the approved Object Design Contract: one stateless function that
# deletes every comment on an issue through the GitHub API and does nothing
# else — no agent, no workflow routing, no comment posting.


def _recording_run(script):
    """Recording ``_run`` double capturing (args, token) per call."""
    calls = []

    def fake_run(args, token=None):
        calls.append((list(args), token))
        joined = " ".join(args)
        for key, response in script.items():
            if key in joined:
                rc, out, err = response
                return _proc(rc, out, err)
        return _proc(0, "", "")

    return fake_run, calls


class TestDeleteIssueCommentsContract:
    def test_signature_is_stable(self):
        import inspect

        sig = inspect.signature(dc.delete_issue_comments)
        assert list(sig.parameters) == ["repo", "issue_number", "token"]
        assert sig.return_annotation is None

    def test_deletes_every_comment_through_the_api(self, monkeypatch):
        fake, calls = _recording_run(
            {
                "--paginate": (0, "100\n200\n", ""),
                "--method DELETE": (0, "", ""),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        assert dc.delete_issue_comments("o/r", 7, "tok") is None
        delete_calls = [c for c in calls if "--method" in c[0]]
        assert [c[0][-1] for c in delete_calls] == [
            "/repos/o/r/issues/comments/100",
            "/repos/o/r/issues/comments/200",
        ]

    def test_uses_the_supplied_token(self, monkeypatch):
        fake, calls = _recording_run({"--paginate": (0, "100\n", "")})
        monkeypatch.setattr(dc, "_run", fake)
        dc.delete_issue_comments("o/r", 7, "secret-token")
        assert calls and all(token == "secret-token" for _, token in calls)

    def test_raises_when_listing_fails(self, monkeypatch):
        fake, _ = _recording_run({"--paginate": (1, "", "HTTP 403: bad creds")})
        monkeypatch.setattr(dc, "_run", fake)
        with pytest.raises(Exception) as exc:
            dc.delete_issue_comments("o/r", 7, "tok")
        # A real API failure, not the placeholder stub body.
        assert not isinstance(exc.value, NotImplementedError)

    def test_raises_when_a_delete_fails(self, monkeypatch):
        fake, _ = _recording_run(
            {
                "--paginate": (0, "100\n", ""),
                "/issues/comments/100": (1, "", "HTTP 500: boom"),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        with pytest.raises(Exception) as exc:
            dc.delete_issue_comments("o/r", 7, "tok")
        assert not isinstance(exc.value, NotImplementedError)

    def test_404_comment_is_skipped(self, monkeypatch):
        # A comment already gone (404) is not a failure: continue the batch.
        fake, _ = _recording_run(
            {
                "--paginate": (0, "100\n200\n", ""),
                "/issues/comments/100": (1, "", "HTTP 404: Not Found"),
                "--method DELETE": (0, "", ""),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        assert dc.delete_issue_comments("o/r", 7, "tok") is None

    def test_posts_no_comment(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        fake, _ = _recording_run(
            {
                "--paginate": (0, "100\n", ""),
                "--method DELETE": (0, "", ""),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        dc.delete_issue_comments("o/r", 7, "tok")
        assert not (tmp_path / "final_message.txt").exists()


# ── main() end-to-end (exit code, no posting) ────────────────────────


class TestMain:
    @pytest.fixture()
    def sandbox(self, tmp_path, monkeypatch):
        old = os.getcwd()
        os.chdir(tmp_path)
        monkeypatch.delenv("DELETE_COMMENTS_REPO", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.delenv("DELETE_COMMENTS_ISSUE", raising=False)
        try:
            yield tmp_path
        finally:
            os.chdir(old)

    def test_missing_contract_exits_nonzero_without_posting(self, sandbox):
        assert dc.main() == 1
        # Pure action: a failed runs posts nothing.
        assert not (sandbox / "final_message.txt").exists()

    def test_successful_batch_exits_zero_without_posting(self, sandbox, monkeypatch):
        fake, _ = _fake_run(
            {"--paginate": (0, "100\n200\n", ""), "--method DELETE": (0, "", "")}
        )
        monkeypatch.setattr(dc, "_run", fake)
        monkeypatch.setenv("DELETE_COMMENTS_REPO", "o/r")
        monkeypatch.setenv("DELETE_COMMENTS_ISSUE", "7")
        assert dc.main() == 0
        assert not (sandbox / "final_message.txt").exists()

    def test_batch_error_exits_nonzero_without_posting(self, sandbox, monkeypatch):
        fake, _ = _fake_run(
            {
                "--paginate": (0, "100\n200\n", ""),
                "/issues/comments/200": (1, "", "HTTP 500: boom"),
                "--method DELETE": (0, "", ""),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        monkeypatch.setenv("DELETE_COMMENTS_REPO", "o/r")
        monkeypatch.setenv("DELETE_COMMENTS_ISSUE", "7")
        assert dc.main() == 1
        assert not (sandbox / "final_message.txt").exists()

    def test_listing_failure_exits_nonzero(self, sandbox, monkeypatch):
        fake, _ = _fake_run({"--paginate": (1, "", "HTTP 403: bad creds")})
        monkeypatch.setattr(dc, "_run", fake)
        monkeypatch.setenv("DELETE_COMMENTS_REPO", "o/r")
        monkeypatch.setenv("DELETE_COMMENTS_ISSUE", "7")
        assert dc.main() == 1
        assert not (sandbox / "final_message.txt").exists()
