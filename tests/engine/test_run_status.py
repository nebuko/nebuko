"""Run-status labels (issue #1336).

The engine publishes one platform-neutral :class:`RunStatus` per run through
the adapter port, from the same seams that emit the notices: a start means
``running``, a human ping means ``gated`` (or ``review-needed`` at the end of a
workflow), and a failure notice means ``failed``. These tests drive a real
``Engine.run()`` over the local adapter, which records the status.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.adapters.base import RunStatus
from nebuko.adapters.local import LocalAdapter
from nebuko.engine.models import ExitCode, Trigger
from nebuko.engine.run_loop import Engine, run_status_for

REPO_ROOT = Path(__file__).resolve().parents[2]


def _trigger(number: int = 1336) -> Trigger:
    return Trigger(
        author="olivier-norture",
        body="@@dev do it",
        type="issue_comment",
        number=number,
        issue_body="",
    )


def _engine(tmp_path: Path, *, name: str = "run-1") -> Engine:
    return Engine(
        adapter=LocalAdapter(tmp_path / "runs" / name, trigger=_trigger()),
        repo_root=REPO_ROOT,
        env={
            "CIRCUIT_BREAKER_THRESHOLD": "3",
            "GITHUB_REPOSITORY": "olivier-norture/nebuko",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "41540909805",
            "TRIGGER_AUTHOR": "olivier-norture",
        },
    )


def _agent_result(dispatch: dict | None = None) -> dict:
    from nebuko.engine.lib.workflow_state import WorkflowState

    return {
        "answer": "## headline\n\nDid the work.",
        "turn_outcome": dispatch or {"appreciation": "approved"},
        "state": WorkflowState(
            workflow="default",
            step="implement",
            agent="dev",
            position=[{"workflow": "default", "step": "implement"}],
        ),
    }


def _stub_resolved(monkeypatch, resolved: dict) -> None:
    import sys

    import nebuko.engine.lib.resolve_config  # noqa: F401

    monkeypatch.setattr(
        sys.modules["nebuko.engine.lib.resolve_config.resolve_config"],
        "resolve_config",
        lambda **_: resolved,
    )


_RESOLVED_MID_WORKFLOW = {
    "SKIP_AGENT": "false",
    "TASK": "dev",
    "WORKFLOW_NAME": "default",
    "WORKFLOW_STEP": "implement",
}

_RESOLVED_TERMINAL = {
    "SKIP_AGENT": "false",
    "TASK": "dev",
    "WORKFLOW_NAME": "default",
    "WORKFLOW_STEP": "review",
}


@pytest.fixture(autouse=True)
def _quiet_git(monkeypatch):
    """Keep the git-bound phases inert: these tests exercise the status seam."""
    from nebuko.engine.lib.git import branch_scaffold, git_session, pr_finalize

    monkeypatch.setattr(
        Engine,
        "_git_session_preflight",
        lambda self: git_session.GuardResult(ok=True, skipped=True),
    )
    monkeypatch.setattr(
        Engine,
        "_branch_scaffold",
        lambda self: branch_scaffold.ScaffoldResult(ok=True, skipped=True),
    )
    monkeypatch.setattr(
        Engine,
        "_git_session_finalize",
        lambda self: git_session.GuardResult(ok=True, skipped=True),
    )
    monkeypatch.setattr(
        pr_finalize.PrFinalizer,
        "finalize",
        lambda self: pr_finalize.PrFinalizeResult(ok=True, skipped=True),
    )


class TestStatusDecision:
    """One pure decision point maps each event to a status."""

    @pytest.mark.parametrize(
        ("event", "kind", "expected"),
        [
            ("started", None, RunStatus.RUNNING),
            ("human_action", "awaiting_approval", RunStatus.GATED),
            ("human_action", "awaiting_input", RunStatus.GATED),
            ("human_action", "workflow_complete", RunStatus.REVIEW_NEEDED),
            ("failure", None, RunStatus.FAILED),
            ("human_action", "hold", None),
        ],
    )
    def test_maps_the_event_to_a_status(self, event, kind, expected):
        assert run_status_for(event, kind=kind) is expected


class TestEngineStatus:
    """Each event sets the expected status through a real ``Engine.run()``."""

    def test_a_started_step_is_running(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED_MID_WORKFLOW))
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result())

        engine.run()

        assert engine.adapter.load_status() is RunStatus.RUNNING

    def test_a_hold_is_gated(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED_MID_WORKFLOW))
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result(
                {"appreciation": "hold", "hold_kind": "awaiting_input", "note": "need input"}
            ),
        )

        engine.run()

        assert engine.adapter.load_status() is RunStatus.GATED

    def test_a_finished_workflow_needs_review(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED_TERMINAL))
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result())

        engine.run()

        assert engine.adapter.load_status() is RunStatus.REVIEW_NEEDED

    def test_a_failure_is_failed(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED_MID_WORKFLOW))
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result())

        def boom(self, agent_result):
            raise RuntimeError("persist exploded")

        monkeypatch.setattr(Engine, "persist", boom)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR
        assert engine.adapter.load_status() is RunStatus.FAILED

    def test_a_script_step_start_is_running(self, tmp_path, monkeypatch):
        import sys

        engine = _engine(tmp_path)
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "RUN_SCRIPT": "true",
                "SCRIPT_PATH": sys.executable,
                "SCRIPT_ARGS": '["-c", "print(\'hello\')"]',
                "WORKFLOW_STEP": "run_tests",
                "WORKFLOW_NAME": "default",
            },
        )
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran")
        )

        engine.run()

        assert engine.adapter.load_status() is RunStatus.RUNNING

    def test_a_full_sequence_leaves_one_status_at_each_step(self, tmp_path, monkeypatch):
        recorded: list[RunStatus] = []
        real = LocalAdapter.set_status

        def recording(self, status):
            recorded.append(status)
            return real(self, status)

        monkeypatch.setattr(LocalAdapter, "set_status", recording)

        # started
        started = _engine(tmp_path, name="run-1")
        _stub_resolved(monkeypatch, dict(_RESOLVED_MID_WORKFLOW))
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result())
        started.run()
        assert started.adapter.load_status() is RunStatus.RUNNING

        # gated
        gated = _engine(tmp_path, name="run-2")
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result(
                {"appreciation": "hold", "hold_kind": "awaiting_input"}
            ),
        )
        gated.run()
        assert gated.adapter.load_status() is RunStatus.GATED

        # started again
        restarted = _engine(tmp_path, name="run-3")
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result())
        restarted.run()
        assert restarted.adapter.load_status() is RunStatus.RUNNING

        # finished
        finished = _engine(tmp_path, name="run-4")
        _stub_resolved(monkeypatch, dict(_RESOLVED_TERMINAL))
        finished.run()
        assert finished.adapter.load_status() is RunStatus.REVIEW_NEEDED

        assert recorded == [
            RunStatus.RUNNING,
            RunStatus.RUNNING,
            RunStatus.GATED,
            RunStatus.RUNNING,
            RunStatus.RUNNING,
            RunStatus.REVIEW_NEEDED,
        ]

    def test_a_failing_set_status_does_not_change_the_outcome(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED_MID_WORKFLOW))
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result())

        def boom(self, status):
            raise RuntimeError("labels unavailable")

        monkeypatch.setattr(LocalAdapter, "set_status", boom)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.turn_outcome.appreciation.value == "approved"
