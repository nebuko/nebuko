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
        fake, _ = _fake_run(
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
        assert result.skipped == ()

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
        monkeypatch.delenv("TRIGGER_BODY", raising=False)
        monkeypatch.delenv("WORKFLOW_POSITION", raising=False)
        try:
            yield tmp_path
        finally:
            os.chdir(old)

    def test_missing_contract_exits_nonzero(self, sandbox):
        assert dc.main() == 1
        assert not os.path.exists("final_message.txt")

    def test_successful_batch_writes_artifacts(self, sandbox, monkeypatch):
        fake, _ = _fake_run(
            {"--paginate": (0, "100\n200\n", ""), "--method DELETE": (0, "", "")}
        )
        monkeypatch.setattr(dc, "_run", fake)
        monkeypatch.setenv("DELETE_COMMENTS_REPO", "o/r")
        monkeypatch.setenv("DELETE_COMMENTS_ISSUE", "7")
        assert dc.main() == 0
        with open("final_message.txt", encoding="utf-8") as f:
            assert "Deleted: 2" in f.read()
        with open("workflow_state.json", encoding="utf-8") as f:
            import json

            state = json.load(f)
        # `status` defaults to "advance" and is dropped by exclude_defaults;
        # absence of a dispatch override means the flow stays alive.
        dispatch = state.get("dispatch") or {}
        assert dispatch.get("status", "advance") == "advance"

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
            assert "Stopped on error" in f.read()
        with open("workflow_state.json", encoding="utf-8") as f:
            import json

            assert json.load(f)["dispatch"]["branch"] == "fail"
        with open("final_message.txt", encoding="utf-8") as f:
            assert "partial" in f.read().lower()

    def test_listing_failure_is_failure_result(self, sandbox, monkeypatch):
        fake, _ = _fake_run({"--paginate": (1, "", "HTTP 403: bad creds")})
        monkeypatch.setattr(dc, "_run", fake)
        monkeypatch.setenv("DELETE_COMMENTS_REPO", "o/r")
        monkeypatch.setenv("DELETE_COMMENTS_ISSUE", "7")
        assert dc.main() == 0
        with open("final_message.txt", encoding="utf-8") as f:
            assert "Failed" in f.read()