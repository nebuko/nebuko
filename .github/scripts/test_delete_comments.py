"""Executor tests for the deterministic delete-comments command (issue #644).

Subprocess layer only is mocked (``delete_comments._run``); the real
filtering/result logic is exercised. Covers the plan's test matrix: full
scope, partial scope, 404-skip-continue, non-404-stop, empty issue, and
env-contract validation errors.
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


# ── delete_comments_from ─────────────────────────────────────────────


class TestDeleteCommentsFrom:
    def test_full_scope_deletes_all(self, monkeypatch):
        fake, calls = _fake_run(
            {
                "--paginate": (0, "100\n200\n", ""),
                "--method DELETE": (0, "", ""),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        result = dc.delete_comments_from("o/r", 7, None)
        assert result.repo == "o/r"
        assert result.issue == 7
        assert result.from_id is None
        assert result.deleted == 2
        assert result.skipped == ()
        assert result.errors == ()
        # One list call + two delete calls.
        assert len(calls) == 3

    def test_partial_scope_only_ids_at_or_after_from(self, monkeypatch):
        fake, calls = _fake_run(
            {
                "--paginate": (0, "100\n150\n200\n", ""),
                "--method DELETE": (0, "", ""),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        result = dc.delete_comments_from("o/r", 7, 150)
        assert result.from_id == 150
        assert result.deleted == 2  # 150 and 200 only; 100 untouched
        deleted_targets = [c for c in calls if "--method" in c]
        assert [c[-1] for c in deleted_targets] == [
            "/repos/o/r/issues/comments/150",
            "/repos/o/r/issues/comments/200",
        ]

    def test_404_skipped_and_batch_continues(self, monkeypatch):
        fake, _ = _fake_run(
            {
                "--paginate": (0, "100\n200\n300\n", ""),
                "/issues/comments/200": (1, "", "HTTP 404: Not Found"),
                "--method DELETE": (0, "", ""),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        result = dc.delete_comments_from("o/r", 7, None)
        assert result.deleted == 2
        assert result.skipped == (200,)
        assert result.errors == ()

    def test_non404_error_stops_batch_with_partial_result(self, monkeypatch):
        fake, calls = _fake_run(
            {
                "--paginate": (0, "100\n200\n300\n", ""),
                "/issues/comments/200": (1, "", "HTTP 500: boom"),
                "--method DELETE": (0, "", ""),
            }
        )
        monkeypatch.setattr(dc, "_run", fake)
        result = dc.delete_comments_from("o/r", 7, None)
        assert result.deleted == 1  # 100 deleted, then stopped at 200
        assert result.errors and "comment 200" in result.errors[0]
        # 300 must NOT have been attempted after the stop.
        delete_calls = [c for c in calls if "--method" in c]
        assert all(not any("300" in arg for arg in c) for c in delete_calls)

    def test_empty_issue_deletes_nothing(self, monkeypatch):
        fake, calls = _fake_run({"--paginate": (0, "", "")})
        monkeypatch.setattr(dc, "_run", fake)
        result = dc.delete_comments_from("o/r", 7, None)
        assert result.deleted == 0
        assert result.errors == ()
        assert len(calls) == 1  # only the listing call

    def test_from_id_beyond_all_ids_deletes_nothing(self, monkeypatch):
        fake, _ = _fake_run({"--paginate": (0, "100\n", "")})
        monkeypatch.setattr(dc, "_run", fake)
        result = dc.delete_comments_from("o/r", 7, 999)
        assert result.deleted == 0


# ── Summary rendering ────────────────────────────────────────────────


class TestRenderSummary:
    def test_all_clear(self):
        r = dc.DeleteCommentsResult("o/r", 7, None, 3, (), ())
        text = dc.render_summary(r)
        assert "issue #7" in text
        assert "Deleted: 3" in text
        assert "all comments" in text

    def test_partial_with_error(self):
        r = dc.DeleteCommentsResult("o/r", 7, 150, 1, (200,), ("failed: x",))
        text = dc.render_summary(r)
        assert "Stopped on error" in text
        assert "from comment 150" in text


# ── read_config ──────────────────────────────────────────────────────


class TestReadConfig:
    def test_defaults_repo_to_github_repository(self):
        cfg = dc.read_config(
            {"GITHUB_REPOSITORY": "o/r", "DELETE_COMMENTS_ISSUE": "7"}
        )
        assert cfg["repo"] == "o/r"
        assert cfg["issue"] == 7
        assert cfg["from_id"] is None

    def test_explicit_repo_wins(self):
        cfg = dc.read_config(
            {
                "DELETE_COMMENTS_REPO": "a/b",
                "GITHUB_REPOSITORY": "o/r",
                "DELETE_COMMENTS_ISSUE": "7",
                "DELETE_COMMENTS_FROM": "5811583860",
            }
        )
        assert cfg["repo"] == "a/b"
        assert cfg["from_id"] == 5811583860

    def test_missing_repo_raises(self):
        with pytest.raises(ValueError, match="DELETE_COMMENTS_REPO"):
            dc.read_config({"DELETE_COMMENTS_ISSUE": "7"})

    def test_missing_issue_raises(self):
        with pytest.raises(ValueError, match="DELETE_COMMENTS_ISSUE"):
            dc.read_config({"DELETE_COMMENTS_REPO": "o/r"})

    def test_non_numeric_issue_raises(self):
        with pytest.raises(ValueError, match="DELETE_COMMENTS_ISSUE"):
            dc.read_config({"DELETE_COMMENTS_REPO": "o/r", "DELETE_COMMENTS_ISSUE": "abc"})

    def test_non_numeric_from_ignored(self):
        cfg = dc.read_config(
            {"DELETE_COMMENTS_REPO": "o/r", "DELETE_COMMENTS_ISSUE": "7", "DELETE_COMMENTS_FROM": "abc"}
        )
        assert cfg["from_id"] is None

    def test_non_ascii_digit_issue_raises(self):
        # '²' passes str.isdigit() but int() rejects it; must raise, not crash.
        with pytest.raises(ValueError, match="DELETE_COMMENTS_ISSUE"):
            dc.read_config({"DELETE_COMMENTS_REPO": "o/r", "DELETE_COMMENTS_ISSUE": "²"})

    def test_non_ascii_digit_from_ignored(self):
        cfg = dc.read_config(
            {"DELETE_COMMENTS_REPO": "o/r", "DELETE_COMMENTS_ISSUE": "7", "DELETE_COMMENTS_FROM": "²"}
        )
        assert cfg["from_id"] is None


# ── main() end-to-end (artifacts + exit code) ────────────────────────


class TestMain:
    @pytest.fixture()
    def sandbox(self, tmp_path, monkeypatch):
        old = os.getcwd()
        os.chdir(tmp_path)
        monkeypatch.delenv("DELETE_COMMENTS_REPO", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.delenv("DELETE_COMMENTS_ISSUE", raising=False)
        monkeypatch.delenv("DELETE_COMMENTS_FROM", raising=False)
        try:
            yield tmp_path
        finally:
            os.chdir(old)

    def test_missing_contract_exits_nonzero_with_failure_summary(self, sandbox):
        # A contract error is still a *result*: the failure summary must be
        # written so the posting pipeline surfaces it on the issue.
        assert dc.main() == 1
        with open("final_message.txt", encoding="utf-8") as f:
            assert "Config error" in f.read()

    def test_successful_batch_writes_summary(self, sandbox, monkeypatch):
        fake, _ = _fake_run(
            {"--paginate": (0, "100\n200\n", ""), "--method DELETE": (0, "", "")}
        )
        monkeypatch.setattr(dc, "_run", fake)
        monkeypatch.setenv("DELETE_COMMENTS_REPO", "o/r")
        monkeypatch.setenv("DELETE_COMMENTS_ISSUE", "7")
        assert dc.main() == 0
        with open("final_message.txt", encoding="utf-8") as f:
            assert "Deleted: 2" in f.read()

    def test_batch_error_is_failure_result_not_crash(self, sandbox, monkeypatch):
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
        assert dc.main() == 0  # result, not crash
        with open("final_message.txt", encoding="utf-8") as f:
            body = f.read()
        assert "Stopped on error" in body
        assert "partial" in body.lower()

    def test_listing_failure_is_failure_result(self, sandbox, monkeypatch):
        fake, _ = _fake_run({"--paginate": (1, "", "HTTP 403: bad creds")})
        monkeypatch.setattr(dc, "_run", fake)
        monkeypatch.setenv("DELETE_COMMENTS_REPO", "o/r")
        monkeypatch.setenv("DELETE_COMMENTS_ISSUE", "7")
        assert dc.main() == 0
        with open("final_message.txt", encoding="utf-8") as f:
            assert "Failed" in f.read()


# ── delete_issue_comments — pure action contract (issue #674) ────────
#
# Spec for the approved Object Design Contract: one stateless function that
# deletes every comment on an issue through the GitHub API and does nothing
# else — no agent, no workflow routing, no comment posting. The body is a
# stub (raise NotImplementedError), so the behavioural tests below are RED
# until the implement step lands; the signature assertion is green now.


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
