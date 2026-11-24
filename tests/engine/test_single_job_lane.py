"""Single-job user-repo lane: StepDriver stop policy and main()-level loop (issue #1457)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dataclasses import replace

from nebuko.adapters.github import GitHubAdapter
from nebuko.engine.finalize import SECTION_CLOSE, SECTION_OPEN
from nebuko.engine.models import ExitCode, RunOutcome
from nebuko.engine.step_driver import (
    BoundaryContext,
    DriverConfig,
    StopPolicy,
    StopReason,
)
from nebuko.engine.lib.github_client import BOT_MARKER
from nebuko.engine.lib.prepare_comment import CONTINUE_TOKEN
from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState
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


def _agent_turn(appreciation: str) -> dict:
    """What ``Engine.invoke_agent`` returns: the answer, turn outcome and state."""
    return {
        # Every declared output section of the default workflow's steps, in the
        # engine's section markers, so the real validator accepts the answer.
        "answer": "# Step\n\n" + "".join(
            SECTION_OPEN.format(name=name) + "\n\nDone.\n" + SECTION_CLOSE + "\n\n"
            for name in ("context_request", "assumptions", "requirements", "decision", "coverage", "questions")
        ),
        "turn_outcome": {
            "appreciation": appreciation,
            **({"hold_kind": "awaiting_input"} if appreciation == "hold" else {}),
        },
        "state": WorkflowState(
            workflow="default",
            step="context",
            agent="context",
            position=[{"workflow": "default", "step": "context"}],
        ),
    }


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
    """Dispatch run with ``Engine.run`` stubbed: the driver sees scripted outcomes."""
    return _loop_handle(tmp_path, monkeypatch, mocker, real_steps=False)


@pytest.fixture
def real_loop(tmp_path, monkeypatch, mocker):
    """Dispatch run through the real ``Engine.run``; only the agent is stubbed."""
    return _loop_handle(tmp_path, monkeypatch, mocker, real_steps=True)


def _init_checkout(path: Path) -> None:
    """A repo with one commit and no remote: the git guards take their benign skip."""
    import subprocess

    for args in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *args], cwd=path, check=True)


def _loop_handle(tmp_path, monkeypatch, mocker, *, real_steps: bool):
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
        return SimpleNamespace(comment_id=str(len(thread.posted)))

    mocker.patch.object(GitHubAdapter, "load_history", side_effect=load_history)
    mocker.patch.object(GitHubAdapter, "post", post)
    if real_steps:
        # The thread fallback stays real (the hand-off is tried first); the posted
        # comments are not in the fake thread, so the fallback finds nothing.
        mocker.patch("nebuko.engine.lib.resolve_config.get_issue_comments", return_value=[])
    else:
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
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.delenv("WORKFLOW_MAX_STEPS", raising=False)

    state = {"script": [], "on_step": None}
    seen_prev: list[WorkflowState | None] = []
    agent_calls: list[str] = []

    def fake_run(self):
        triggers.append((self.adapter.read_trigger().body, self.adapter.read_trigger().step))
        markers.append(os.environ.get("WORKFLOW_NO_CONTINUE", ""))
        if state["on_step"] is not None:
            state["on_step"](len(triggers), thread)
        code = state["script"][len(triggers) - 1]
        if len(triggers) > 1:
            seen_prev.append(self.adapter.load_state())
        make_state = state.get("state_for")
        outcome = _outcome(code)
        return replace(outcome, state=make_state(len(triggers))) if make_state else outcome

    run = None
    if real_steps:
        _init_checkout(tmp_path)
        agent_turns: list[str] = state.setdefault("agent_turns", [])

        def fake_agent(self, prompt):
            appreciation = agent_turns.pop(0) if agent_turns else "approved"
            _st = WorkflowState.model_validate(self.adapter.load_state().model_dump()) if self.adapter.load_state() else None
            print('DBG', len(agent_calls)+1, appreciation, _st and (_st.step, _st.agent, _st.turn_outcome), file=__import__('sys').stderr)
            if state["on_step"] is not None:
                state["on_step"](len(agent_calls) + 1, thread)
            agent_calls.append(prompt)
            return _agent_turn(appreciation)

        mocker.patch.object(run_loop.Engine, "invoke_agent", autospec=True, side_effect=fake_agent)
    else:
        run = mocker.patch.object(run_loop.Engine, "run", autospec=True, side_effect=fake_run)

    class Handle:
        pass

    handle = Handle()
    handle.thread = thread
    handle.triggers = triggers
    handle.markers = markers
    handle.run = run
    handle.state = state
    handle.seen_prev = seen_prev
    handle.agent_calls = agent_calls
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


class TestHandOffAndArtifacts:
    def test_next_step_reads_the_previous_steps_state_from_the_handoff(self, loop):
        loop.state["script"] = [ExitCode.ADVANCE, ExitCode.ADVANCE, ExitCode.HOLD]
        loop.state["state_for"] = lambda n: WorkflowState(
            workflow="default", step=f"s{n}", agent="context", position=[]
        )

        loop.main()

        assert [s.step for s in loop.seen_prev] == ["s1", "s2"]

    def test_console_and_timings_keep_one_record_per_step(self, tmp_path, monkeypatch):
        from nebuko.engine.artifacts import CONSOLE_LOG_FILE, TIMINGS_FILE, RunArtifactStager
        from nebuko.engine.lib.loop_step import LOOP_STEP_ENV

        stager = RunArtifactStager(tmp_path)
        for step in (1, 2, 3):
            monkeypatch.setenv(LOOP_STEP_ENV, str(step))
            stager.bundle(f"console of {step}")
            stager.stage_timings({"engine_init": float(step)})

        console = (tmp_path / CONSOLE_LOG_FILE).read_text(encoding="utf-8")
        assert "console of 1" in console
        assert "=== step 3 ===\nconsole of 3" in console
        records = json.loads((tmp_path / TIMINGS_FILE).read_text(encoding="utf-8"))
        assert [r["step"] for r in records] == [1, 2, 3]

    def test_metrics_file_is_per_step_in_the_loop(self, tmp_path, monkeypatch):
        from nebuko.engine.lib.agent_script.features.collect_metrics import collect_metrics
        from nebuko.engine.lib.loop_step import LOOP_STEP_ENV
        from nebuko.engine.lib.metrics_schema import Metrics

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(LOOP_STEP_ENV, "2")

        collect_metrics.save_metrics_json(Metrics(run_id="abc"))

        assert (tmp_path / "output" / "metrics-abc-step-2.json").is_file()


class TestRealStepPath:
    def test_three_real_steps_post_no_continue_token(self, real_loop):
        real_loop.state["agent_turns"] = ["approved", "approved", "hold"]

        assert real_loop.main() == 0, [b[:300] for b in real_loop.thread.posted]

        assert len(real_loop.agent_calls) == 3
        assert real_loop.thread.posted
        assert all(CONTINUE_TOKEN not in body for body in real_loop.thread.posted)
        assert not any("nebuko:refused" in body or "nebuko:failed" in body
                       for body in real_loop.thread.posted)

    def test_human_comment_stops_the_real_loop_at_the_next_boundary(self, real_loop):
        def human_speaks(step, thread):
            if step == 2:
                thread.add("777", "/nebuko approve gate")

        real_loop.state["on_step"] = human_speaks

        real_loop.main()

        assert len(real_loop.agent_calls) == 2
        assert "777" in [c.comment_id for c in real_loop.thread.comments]
        assert all(CONTINUE_TOKEN not in body for body in real_loop.thread.posted)

