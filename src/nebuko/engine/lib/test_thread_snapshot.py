"""Tests for thread_snapshot.py — the per-run bounded thread read (issue #805)."""

import json
from unittest.mock import MagicMock, patch

from nebuko.engine.lib.github_client import GitHubTransport
from nebuko.engine.lib.thread_snapshot import (
    STATE_TAIL_FLOOR,
    ThreadSnapshot,
    build_comment_index,
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
    from nebuko.engine.lib.workflow_state.workflow_state import STATE_MARKER_END, STATE_MARKER_START

    return f"{STATE_MARKER_START}\n{state_json}\n{STATE_MARKER_END}"


def _state_json(*, history_len=1):
    from nebuko.engine.lib.config import StepRef
    from nebuko.engine.lib.workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
    from nebuko.engine.lib.workflow_state.sub_domains.turn_outcome.turn_outcome import (
        Appreciation,
        TurnOutcome,
        TurnStatus,
    )
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

    state = WorkflowState(
        version=7,
        workflow="default",
        step="plan",
        agent="architect",
        position=[StepRef(workflow="default", step="plan")],
        history=[
            HistoryEntry(
                step=f"step_{index}",
                agent="architect",
                summary=f"s{index}",
                turn_outcome=TurnOutcome(
                    status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED
                ),
            )
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

    @patch("nebuko.engine.lib.github_client.subprocess.run")
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

    @patch("nebuko.engine.lib.github_client.subprocess.run")
    def test_single_page_thread_needs_one_request(self, mock_run):
        mock_run.return_value = _ok_page([{"id": 1, "body": "only"}])

        snapshot = load_thread_snapshot("o/r", 42, limit=5, transport=_transport())

        assert [comment["id"] for comment in snapshot.comments] == [1]
        assert mock_run.call_count == 1

    @patch("nebuko.engine.lib.github_client.subprocess.run")
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

    @patch("nebuko.engine.lib.github_client.subprocess.run")
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


class TestBuildCommentIndex:
    """``build_comment_index`` maps comment id -> state-stripped body (#756)."""

    def test_maps_id_to_body(self):
        index = build_comment_index([{"id": 5, "body": "hello"}])
        assert index == {"5": "hello"}

    def test_strips_the_state_section(self):
        from nebuko.engine.lib.workflow_state import WorkflowState, to_git_comment

        state = WorkflowState(workflow="w", step="plan", agent="architect")
        body = f"agent answer\n\n{to_git_comment(state)}"
        index = build_comment_index([{"id": 7, "body": body}])
        assert "WORKFLOW_STATE" not in index["7"]
        assert index["7"].startswith("agent answer")

    def test_html_url_keys_the_same_body(self):
        index = build_comment_index(
            [{"id": 5, "html_url": "https://x/5", "body": "hi"}]
        )
        assert index["5"] == index["https://x/5"] == "hi"

    def test_comments_without_id_are_skipped(self):
        assert build_comment_index([{"body": "hi"}]) == {}

    def test_missing_body_is_empty_string(self):
        assert build_comment_index([{"id": 1}]) == {"1": ""}
