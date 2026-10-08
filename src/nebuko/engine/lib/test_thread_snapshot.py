"""Tests for thread_snapshot.py — the per-run bounded thread read (issue #805)."""

import json
from unittest.mock import MagicMock, patch

from github_client import GitHubTransport
from thread_snapshot import (
    STATE_TAIL_FLOOR,
    ThreadSnapshot,
    build_snapshot,
    load_thread_snapshot,
    state_tail_limit,
)


def _transport(**kwargs):
    kwargs.setdefault("max_attempts", 1)
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return GitHubTransport(**kwargs)


def _ok_page(comments, last_page=None):
    headers = ""
    if last_page is not None:
        headers = (
            f'\nLink: <https://api.github.com/x?page={last_page}>; rel="last"'
        )
    head = "HTTP/2.0 200 OK" + headers
    return MagicMock(returncode=0, stdout=f"{head}\n\n{json.dumps(comments)}", stderr="")


def _state_body(state_json):
    from workflow_state.workflow_state import STATE_MARKER_END, STATE_MARKER_START

    return f"{STATE_MARKER_START}\n{state_json}\n{STATE_MARKER_END}"


def _state_json(*, history_len=1):
    from config import StepRef
    from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
    from workflow_state.workflow_state import WorkflowState

    state = WorkflowState(
        version=7,
        workflow="default",
        step="plan",
        agent="genius",
        position=[StepRef(workflow="default", step="plan")],
        history=[
            HistoryEntry(step=f"step_{index}", agent="genius", summary=f"s{index}")
            for index in range(history_len)
        ],
    )
    return state, state.model_dump_json()


class TestBuildSnapshot:
    """``build_snapshot`` extracts state once and survives a failed read."""

    def test_extracts_the_latest_state_from_raw_comments(self):
        state, payload = _state_json(history_len=2)

        snapshot = build_snapshot(
            [
                {"body": "<!-- bot-workflow-terminal -->\nchatter"},
                {"body": _state_body(payload)},
            ]
        )

        assert isinstance(snapshot, ThreadSnapshot)
        assert snapshot.fetch_error is None
        assert snapshot.state is not None
        assert snapshot.state.workflow == state.workflow
        assert [entry.step for entry in snapshot.state.history] == ["step_0", "step_1"]

    def test_keeps_comment_dicts_untouched(self):
        comments = [{"id": 3, "body": "c", "user": {"login": "x"}}]

        snapshot = build_snapshot(comments)

        assert snapshot.comments == tuple(comments)

    def test_a_fetch_error_is_never_an_empty_thread(self):
        snapshot = build_snapshot((), fetch_error="403 secondary rate limit")

        assert snapshot.comments == ()
        assert snapshot.state is None
        assert snapshot.fetch_error == "403 secondary rate limit"


class TestLoadThreadSnapshot:
    """``load_thread_snapshot`` performs one bounded, tail-only read."""

    @patch("github_client.subprocess.run")
    def test_tail_read_jumps_to_the_last_page_only(self, mock_run):
        def fake_run(cmd, **_kwargs):
            joined = " ".join(cmd)
            if "page=3" in joined:
                return _ok_page([{"id": 5, "body": "c5"}, {"id": 6, "body": "c6"}])
            return _ok_page([{"id": 1, "body": "c1"}], last_page=3)

        mock_run.side_effect = fake_run

        snapshot = load_thread_snapshot("o/r", 42, limit=2, transport=_transport())

        assert [comment["id"] for comment in snapshot.comments] == [5, 6]
        # One discovery request plus the last page — never the whole thread.
        assert mock_run.call_count == 2

    @patch("github_client.subprocess.run")
    def test_single_page_thread_needs_one_request(self, mock_run):
        mock_run.return_value = _ok_page([{"id": 1, "body": "only"}])

        snapshot = load_thread_snapshot("o/r", 42, limit=5, transport=_transport())

        assert [comment["id"] for comment in snapshot.comments] == [1]
        assert mock_run.call_count == 1

    @patch("github_client.subprocess.run")
    def test_surfaces_a_failed_read_as_fetch_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="gh: You have exceeded a secondary rate limit (HTTP 403)"
        )

        snapshot = load_thread_snapshot("o/r", 42, limit=5, transport=_transport())

        assert snapshot.comments == ()
        assert snapshot.state is None
        assert snapshot.fetch_error == "gh: You have exceeded a secondary rate limit (HTTP 403)"

    def test_state_tail_floor_is_a_single_page(self):
        assert 0 < STATE_TAIL_FLOOR <= 100

    @patch("github_client.subprocess.run")
    def test_extracts_state_from_the_fetched_tail(self, mock_run):
        state, payload = _state_json()
        mock_run.return_value = _ok_page([{"body": _state_body(payload)}])

        snapshot = load_thread_snapshot("o/r", 42, limit=5, transport=_transport())

        assert snapshot.state is not None
        assert snapshot.state.step == state.step


class TestStateTailLimit:
    """``state_tail_limit`` keeps a state-only read to a single page."""

    def test_defaults_to_the_floor_when_unset(self):
        assert state_tail_limit({}) == STATE_TAIL_FLOOR

    def test_floors_a_small_history_bound(self):
        assert state_tail_limit({"HISTORY_COMMENT_COUNT": "5"}) == STATE_TAIL_FLOOR

    def test_honours_a_larger_history_bound(self):
        assert state_tail_limit({"HISTORY_COMMENT_COUNT": "250"}) == 250

    def test_ignores_a_non_numeric_bound(self):
        assert state_tail_limit({"HISTORY_COMMENT_COUNT": "many"}) == STATE_TAIL_FLOOR

    def test_floors_a_non_positive_bound(self):
        assert state_tail_limit({"HISTORY_COMMENT_COUNT": "0"}) == STATE_TAIL_FLOOR
