"""Single-job user-repo lane: StepDriver stop policy and main()-level loop (issue #1457)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nebuko.adapters.github import GitHubAdapter
from nebuko.engine.models import ExitCode, RunOutcome
from nebuko.engine.step_driver import (
    BoundaryContext,
    DriverConfig,
    StopPolicy,
    StopReason,
)
from nebuko.engine.lib.github_client import BOT_MARKER
from nebuko.engine.models import CommentEntry


def _outcome(code: ExitCode) -> RunOutcome:
    return RunOutcome(
        exit_code=code,
        turn_outcome=None,
        prompt=None,
        posted_body=None,
        state=None,
        phases=(),
    )


def _cfg(**overrides) -> DriverConfig:
    base = {"enabled": True, "max_steps": 6, "wall_clock_s": 3000.0, "step_budget_s": 1200.0}
    base.update(overrides)
    return DriverConfig(**base)


def _ctx(step: int = 1, elapsed: float = 0.0, new: frozenset[str] = frozenset()) -> BoundaryContext:
    return BoundaryContext(
        step_index=step,
        elapsed_s=elapsed,
        comment_ids_at_start=frozenset({"1"}),
        comment_ids_now=frozenset({"1"}) | new,
    )


class TestStopPolicy:
    @pytest.mark.parametrize(
        ("code", "reason"),
        [
            (ExitCode.HOLD, StopReason.HOLD),
            (ExitCode.TERMINAL, StopReason.TERMINAL),
            (ExitCode.ERROR, StopReason.NOT_ADVANCING),
        ],
    )
    def test_non_advance_outcomes_stop_with_their_reason(self, code, reason):
        assert StopPolicy(_cfg()).decide(_outcome(code), _ctx()) is reason

    def test_advance_continues(self):
        assert StopPolicy(_cfg()).decide(_outcome(ExitCode.ADVANCE), _ctx()) is None

    def test_new_human_comment_stops(self):
        decision = StopPolicy(_cfg()).decide(
            _outcome(ExitCode.ADVANCE), _ctx(new=frozenset({"9"}))
        )
        assert decision is StopReason.HUMAN_COMMENT

    def test_step_cap_stops_at_the_cap(self):
        policy = StopPolicy(_cfg(max_steps=2))
        assert policy.decide(_outcome(ExitCode.ADVANCE), _ctx(step=1)) is None
        assert policy.decide(_outcome(ExitCode.ADVANCE), _ctx(step=2)) is StopReason.STEP_CAP

    def test_next_step_that_would_outlive_the_token_stops(self):
        policy = StopPolicy(_cfg(wall_clock_s=3000.0, step_budget_s=1200.0))
        assert policy.decide(_outcome(ExitCode.ADVANCE), _ctx(elapsed=1800.0)) is None
        assert (
            policy.decide(_outcome(ExitCode.ADVANCE), _ctx(elapsed=1801.0))
            is StopReason.WALL_CLOCK
        )


class TestDriverConfig:
    def test_user_repo_dispatch_is_on_by_default(self):
        cfg = DriverConfig.from_env({}, lane="user-repo", event="repository_dispatch")
        assert cfg.enabled and cfg.max_steps == 6

    def test_other_lanes_keep_one_step(self):
        assert not DriverConfig.from_env({}, lane="org", event="repository_dispatch").enabled
        assert not DriverConfig.from_env({}, lane="user-repo", event="issue_comment").enabled

    def test_single_step_payload_turns_the_loop_off(self):
        cfg = DriverConfig.from_env(
            {}, lane="user-repo", event="repository_dispatch", payload={"single_step": True}
        )
        assert not cfg.enabled

    def test_switch_and_budget_env(self):
        env = {
            "WORKFLOW_SINGLE_JOB": "0",
            "WORKFLOW_MAX_STEPS": "3",
            "WORKFLOW_WALL_CLOCK_S": "600",
        }
        cfg = DriverConfig.from_env(env, lane="user-repo", event="repository_dispatch")
        assert not cfg.enabled and cfg.max_steps == 3 and cfg.wall_clock_s == 600.0


# ---------------------------------------------------------------------------
# main()-level: Engine.run is stubbed per step; the thread is a fake list.
# ---------------------------------------------------------------------------


class _Thread:
    def __init__(self) -> None:
        self.comments: list[CommentEntry] = []
        self.posted: list[str] = []
        self.probe_error: Exception | None = None

    def add(self, comment_id: str, body: str, author: str = "olivier-norture") -> None:
        self.comments.append(
            CommentEntry(author=author, body=body, created_at="2026-11-24T00:00:00Z", comment_id=comment_id)
        )


@pytest.fixture
def loop(tmp_path, monkeypatch, mocker):
    """Patched dispatch run: returns (thread, triggers, run_mock, script-setter)."""
    import nebuko.engine.run_loop as run_loop

    thread = _Thread()
    triggers: list[tuple[str, str | None]] = []
    markers: list[str] = []

    def load_history(**_kwargs):
        if thread.probe_error is not None:
            raise thread.probe_error
        return list(thread.comments)

    def post(_self, body):
        thread.posted.append(body)

    mocker.patch.object(GitHubAdapter, "load_history", side_effect=load_history)
    mocker.patch.object(GitHubAdapter, "post", post)
    mocker.patch(
        "nebuko.engine.lib.resolve_config.resolve_config._fetch_workflow_state_from_history",
        return_value=None,
    )
    mocker.patch(
        "nebuko.adapters.github.GitHubAdapter.load_context_entries", return_value=[]
    )
    mocker.patch(
        "nebuko.adapters.github.GitHubAdapter.load_raw_thread", return_value=(None, [])
    )
    mocker.patch("nebuko.engine.lib.github_client.get_issue", return_value={})

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "client_payload": {
                    "actor": "olivier-norture",
                    "prompt_body": "/nebuko run workflow",
                    "issue_number": 1457,
                    "org_repo": "olivier-norture/git-flow-ai",
                    "step": "context",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "repository_dispatch")
    monkeypatch.setenv("GITHUB_REPOSITORY", "olivier-norture/git-flow-ai")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_SINGLE_JOB", "1")
    monkeypatch.delenv("WORKFLOW_MAX_STEPS", raising=False)

    state = {"script": [], "on_step": None}

    def fake_run(self):
        triggers.append((self.adapter.read_trigger().body, self.adapter.read_trigger().step))
        markers.append(os.environ.get("WORKFLOW_NO_CONTINUE", ""))
        if state["on_step"] is not None:
            state["on_step"](len(triggers), thread)
        code = state["script"][len(triggers) - 1]
        return _outcome(code)

    run = mocker.patch.object(run_loop.Engine, "run", autospec=True, side_effect=fake_run)

    class Handle:
        pass

    handle = Handle()
    handle.thread = thread
    handle.triggers = triggers
    handle.markers = markers
    handle.run = run
    handle.state = state
    handle.main = lambda: run_loop.main(["--adapter", "github", "--repo", "olivier-norture/git-flow-ai"])
    return handle


class TestSingleJobLoop:
    def test_three_steps_run_in_one_invocation_and_stop_at_the_gate(self, loop):
        loop.state["script"] = [ExitCode.ADVANCE, ExitCode.ADVANCE, ExitCode.HOLD]

        assert loop.main() == 0

        assert loop.run.call_count == 3
        assert loop.thread.posted == []  # the engine owns the HOLD notice, not the driver
        assert all(marker == "1" for marker in loop.markers)

    def test_continuation_steps_never_reseed_the_payload_step(self, loop):
        loop.state["script"] = [ExitCode.ADVANCE, ExitCode.ADVANCE, ExitCode.HOLD]

        loop.main()

        assert loop.triggers[0] == ("/nebuko run workflow", "context")
        assert loop.triggers[1:] == [("", None), ("", None)]

    def test_human_comment_stops_the_loop_and_stays_in_the_thread(self, loop):
        loop.state["script"] = [ExitCode.ADVANCE, ExitCode.ADVANCE, ExitCode.HOLD]

        def human_speaks(_step, thread):
            thread.add("555", "/nebuko approve gate")

        loop.state["on_step"] = human_speaks

        loop.main()

        assert loop.run.call_count == 1
        assert [c.comment_id for c in loop.thread.comments] == ["555"]
        (notice,) = loop.thread.posted
        assert notice.startswith(BOT_MARKER)
        assert "/nebuko resume" in notice
        assert "workflow-continue" not in notice

    def test_error_outcome_stops_without_a_driver_notice(self, loop):
        loop.state["script"] = [ExitCode.ADVANCE, ExitCode.ERROR, ExitCode.ADVANCE]

        loop.main()

        assert loop.run.call_count == 2
        assert loop.thread.posted == []

    def test_step_cap_posts_a_notice(self, loop, monkeypatch):
        monkeypatch.setenv("WORKFLOW_MAX_STEPS", "2")
        loop.state["script"] = [ExitCode.ADVANCE] * 3

        loop.main()

        assert loop.run.call_count == 2
        (notice,) = loop.thread.posted
        assert "step cap" in notice

    def test_expired_token_at_the_boundary_is_a_clean_stop(self, loop):
        loop.state["script"] = [ExitCode.ADVANCE, ExitCode.ADVANCE]

        def token_dies(_step, thread):
            thread.probe_error = RuntimeError("401 Bad credentials")

        loop.state["on_step"] = token_dies

        loop.main()

        assert loop.run.call_count == 1
        (notice,) = loop.thread.posted
        assert "Token expiring, re-dispatch" in notice

    def test_single_step_dispatch_runs_one_step(self, loop):
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        event = json.loads(event_path.read_text(encoding="utf-8"))
        event["client_payload"]["single_step"] = True
        event_path.write_text(json.dumps(event), encoding="utf-8")
        loop.state["script"] = [ExitCode.ADVANCE, ExitCode.ADVANCE]

        loop.main()

        assert loop.run.call_count == 1
        assert loop.thread.posted == []


class TestContinueTokenOutsideTheLoop:
    def test_org_lane_step_keeps_the_continue_token(self, loop, monkeypatch):
        monkeypatch.setenv("WORKFLOW_SINGLE_JOB", "0")
        loop.state["script"] = [ExitCode.ADVANCE]

        loop.main()

        assert loop.run.call_count == 1
        assert loop.markers == [""]

    def test_single_step_dispatch_keeps_the_continue_token(self, loop):
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        event = json.loads(event_path.read_text(encoding="utf-8"))
        event["client_payload"]["single_step"] = True
        event_path.write_text(json.dumps(event), encoding="utf-8")
        loop.state["script"] = [ExitCode.ADVANCE]

        loop.main()

        assert loop.markers == [""]

    def test_loop_steps_run_without_the_continue_token(self, loop):
        loop.state["script"] = [ExitCode.ADVANCE, ExitCode.ADVANCE, ExitCode.HOLD]

        loop.main()

        assert loop.markers == ["1", "1", "1"]

    def test_live_header_keeps_the_token_unless_the_loop_is_on(self, monkeypatch):
        from nebuko.engine.lib import prepare_comment as pc

        monkeypatch.delenv("WORKFLOW_NO_CONTINUE", raising=False)
        assert pc._live_header_line() == pc.LIVE_HEADER_LINE
        assert pc.CONTINUE_TOKEN in pc._live_header_line()
        monkeypatch.setenv("WORKFLOW_NO_CONTINUE", "1")
        assert pc._live_header_line() == pc.BOT_MARKER


class TestDriverHelpers:
    def test_auth_error_is_matched_by_status_not_by_text(self):
        from nebuko.engine.step_driver import _is_auth_error

        class _Status(Exception):
            status = 401

        assert _is_auth_error(_Status("nope"))
        assert _is_auth_error(RuntimeError("401 Bad credentials"))
        assert not _is_auth_error(RuntimeError("retry in 401 seconds"))
        assert not _is_auth_error(RuntimeError("HTTP 503"))

    def test_step_budget_comes_from_the_engine_budget(self):
        from nebuko.engine.step_driver import _step_budget_s

        assert _step_budget_s({}) == (20 + 8) * 60
        env = {"AGENT_SOFT_TIMEOUT_MINUTES": "10", "WIND_DOWN_TIMEOUT_MINUTES": "5"}
        assert _step_budget_s(env) == 15 * 60
