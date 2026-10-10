"""Phase-isolation regression for the soft-kill crash (issue #766).

The graceful soft-kill wind-down made the workflow exit 1: the engine's
``publish`` phase raised ``ModuleNotFoundError`` for the legacy
``prepare_comment`` bridge, and the single blanket ``try/except`` around the
phase chain aborted the run *before* ``state`` and ``route``. The ``soft_kill``
dispatch built by ``build_output`` was therefore never journaled, so the run
returned ``ExitCode.ERROR`` and the whole workflow died instead of retrying the
step in place.

``Engine._run_phase`` runs the best-effort phases (``validate``, ``publish``)
in isolation: a crash there can no longer suppress the state journal or the
routing decision.
"""

from __future__ import annotations

import pytest

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.models import ExitCode, Trigger
from nebuko.engine.paths import repo_root
from nebuko.engine.run_loop import Engine


def _trigger(body: str = "@@dev continue", number: int = 766) -> Trigger:
    return Trigger(author="olivier-norture", body=body, type="issue_comment", number=number)


def _engine(tmp_path) -> Engine:
    adapter = LocalAdapter(tmp_path / "runs" / "run-1", trigger=_trigger())
    env = {
        "CIRCUIT_BREAKER_THRESHOLD": "3",
        "GITHUB_REPOSITORY": "olivier-norture/git-flow-ai",
    }
    return Engine(adapter=adapter, repo_root=repo_root(), env=env)


def _state():
    from workflow_state.workflow_state import WorkflowState

    return WorkflowState(run_id=13, workflow="default", step="plan", agent="dev")


def _soft_kill_result(answer: str = "work in progress"):
    """The artefact ``build_output`` emits after the graceful wind-down."""
    return {
        "answer": answer,
        "deliverable": answer,
        "state": _state(),
        "dispatch": {"status": "soft_kill", "reason": "soft time-kill"},
    }


def _raise_publish(self, agent_result):
    raise ModuleNotFoundError("No module named 'prepare_comment'")


class TestPhaseIsolation:
    def test_a_publish_crash_still_persists_the_state_and_routes(
        self, tmp_path, monkeypatch
    ):
        """The D1 invariant: a best-effort crash never suppresses the journal."""
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _soft_kill_result()
        )
        monkeypatch.setattr(Engine, "publish", _raise_publish)

        seen: list[str] = []
        real_persist, real_route = Engine.persist, Engine.route

        def spy_persist(self, agent_result):
            seen.append("persist")
            return real_persist(self, agent_result)

        def spy_route(self, agent_result):
            seen.append("route")
            return real_route(self, agent_result)

        monkeypatch.setattr(Engine, "persist", spy_persist)
        monkeypatch.setattr(Engine, "route", spy_route)

        outcome = engine.run()

        assert seen == ["persist", "route"]
        assert outcome.state is not None
        assert (tmp_path / "runs" / "run-1" / "state.json").is_file()
        assert (tmp_path / "runs" / "run-1" / "journal.md").is_file()

    def test_a_publish_crash_routes_on_the_soft_kill_dispatch(self, tmp_path, monkeypatch):
        """The run must not turn the soft-kill dispatch into ``ExitCode.ERROR``."""
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _soft_kill_result()
        )
        monkeypatch.setattr(Engine, "publish", _raise_publish)

        outcome = engine.run()

        assert outcome.exit_code != ExitCode.ERROR
        assert outcome.routing.status == "soft_kill"
        assert "state" in outcome.phases
        assert "route" in outcome.phases

    def test_a_publish_crash_posts_a_minimal_notice(self, tmp_path, monkeypatch):
        """``publish`` is best-effort: the thread is still left a resumable note."""
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _soft_kill_result("half done")
        )
        monkeypatch.setattr(Engine, "publish", _raise_publish)

        outcome = engine.run()

        assert outcome.posted_body is not None
        assert "could not prepare the agent's comment" in outcome.posted_body
        assert "half done" in outcome.posted_body
        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert "could not prepare the agent's comment" in journal

    def test_the_fallback_never_re_arms_the_agent_trigger(self, tmp_path, monkeypatch):
        """The crash notice is escaped like any other comment (issue #482)."""
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _soft_kill_result("ping @@dev and @@architect"),
        )
        monkeypatch.setattr(Engine, "publish", _raise_publish)

        outcome = engine.run()

        assert "@@dev" not in outcome.posted_body
        assert "@@\u200bdev" in outcome.posted_body
