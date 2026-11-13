"""A provider failure stops the run and reports the real cause (issue #1322).

Run 42049492853 used a wrong provider URL: the first model call returned a 404,
the agent layer swallowed it and synthesized a comment, and the engine carried
on through ``validate``/``state``/``publish``/``route``. The empty-report check
then replaced the real cause with a validation error, in the wrong phase.

These tests pin the terminal path: a provider failure ends the run at the agent
phase, posts exactly one notice naming the provider, model, agent phase and
error, records the same facts on ``engine_failure``, and stays resumable
without consuming a retry budget.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.models import ExitCode
from nebuko.engine.run_loop import Engine
from nebuko.engine.lib.git import git_session
from nebuko.engine.lib.workflow_state import IncompleteReason, WorkflowState
from nebuko.engine.lib.workflow_state.workflow_state import (
    STATE_MARKER_END,
    STATE_MARKER_START,
)
from nebuko.engine.lib.agent_script.features.run_agent.run_failure import ProviderFailure

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / "defaults" / "workflows"
LOCAL_WORKFLOWS_DIR = REPO_ROOT / ".nebuko" / "workflows"

CONTINUE_TOKEN = "<!-- workflow-continue -->"
FAILURE_MARKER = "<!-- bot-engine-failure -->"
API_KEY = "sk-live-should-never-appear"
PROVIDER = "https://wrong.example.test/v1"
MODEL = "glm-5.3-flash"
PROVIDER_ERROR = "litellm.NotFoundError: Error code: 404 - model not found"


def _provider_failure(**overrides) -> ProviderFailure:
    fields = {
        "provider": PROVIDER,
        "model": MODEL,
        "agent_phase": "agent",
        "requires_config_fix": True,
    }
    fields.update(overrides)
    return ProviderFailure(PROVIDER_ERROR, **fields)


def _trigger():
    from nebuko.engine.models import Trigger

    return Trigger(
        author="olivier-norture",
        body="@@dev do it",
        type="issue_comment",
        number=1322,
        issue_body="",
    )


def _engine(tmp_path: Path) -> Engine:
    return Engine(
        adapter=LocalAdapter(tmp_path / "runs" / "run-1", trigger=_trigger()),
        repo_root=tmp_path,
        env={
            "CIRCUIT_BREAKER_THRESHOLD": "3",
            "GITHUB_REPOSITORY": "olivier-norture/nebuko",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "42049492853",
            "TRIGGER_AUTHOR": "olivier-norture",
            # The configured provider secret: the failure text may echo it.
            "TEST_PROVIDER_API_KEY": API_KEY,
        },
    )


def _prior_state() -> WorkflowState:
    return WorkflowState(
        workflow="spec-tdd",
        step="spec",
        agent="po",
        position=[{"workflow": "spec-tdd", "step": "spec"}],
        started_by="olivier-norture",
    )


@pytest.fixture(autouse=True)
def _quiet_git(monkeypatch):
    """Keep the git-bound phases inert unless a test opts in."""
    from nebuko.engine.lib.git import branch_scaffold, pr_finalize

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
    monkeypatch.setattr(Engine, "_pending_worktree_paths", lambda self: ())
    monkeypatch.setattr(
        pr_finalize.PrFinalizer,
        "finalize",
        lambda self: pr_finalize.PrFinalizeResult(ok=True, skipped=True),
    )


def _stub_resolved(monkeypatch, resolved: dict) -> None:
    import sys

    import nebuko.engine.lib.resolve_config  # noqa: F401

    monkeypatch.setattr(
        sys.modules["nebuko.engine.lib.resolve_config.resolve_config"],
        "resolve_config",
        lambda **_: resolved,
    )


_RESOLVED = {
    "SKIP_AGENT": "false",
    "TASK": "dev",
    "WORKFLOW_NAME": "spec-tdd",
    "WORKFLOW_STEP": "spec",
}


def _run_provider_failure(tmp_path, monkeypatch, *, failure=None, seed_state=True):
    """Run the engine with an agent seam that raises a provider failure."""
    engine = _engine(tmp_path)
    _stub_resolved(monkeypatch, dict(_RESOLVED))
    if seed_state:
        engine.adapter.save_state(_prior_state())

    def _boom(self, prompt):
        raise failure or _provider_failure()

    monkeypatch.setattr(Engine, "invoke_agent", _boom)
    return engine, engine.run()


def _journal(tmp_path: Path) -> str:
    return (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")


def _saved_state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "runs" / "run-1" / "state.json").read_text())


class TestProviderFailureTimings:
    """The failed run reports where its wall-clock went (issue #1375)."""

    def test_outcome_carries_the_phase_timings(self, tmp_path, monkeypatch):
        _, outcome = _run_provider_failure(tmp_path, monkeypatch)

        assert outcome.timings
        assert {"normalize", "resolve_config", "guards", "prompt", "agent"} <= set(
            outcome.timings
        )
        assert all(duration >= 0.0 for duration in outcome.timings.values())

    def test_failure_comment_shows_the_time_spent(self, tmp_path, monkeypatch):
        _run_provider_failure(tmp_path, monkeypatch)

        journal = _journal(tmp_path)
        assert "Time spent: " in journal
        assert "agent=" in journal  # the phase that failed is included

    def test_timings_artifact_is_staged(self, tmp_path, monkeypatch):
        _run_provider_failure(tmp_path, monkeypatch)

        staged = tmp_path / "runs" / "run-1" / "timings.json"
        assert json.loads(staged.read_text(encoding="utf-8"))["agent"] >= 0.0


class TestProviderFailureStopsTheRun:
    def test_first_call_provider_failure_stops_before_validate(
        self, tmp_path, monkeypatch
    ):
        _, outcome = _run_provider_failure(tmp_path, monkeypatch)

        assert "validate" not in outcome.phases
        assert "publish" not in outcome.phases
        assert "route" not in outcome.phases
        # Nothing that carries a continue token was posted.
        assert CONTINUE_TOKEN not in _journal(tmp_path)

    def test_run_exits_failed_and_never_logs_success(
        self, tmp_path, monkeypatch, caplog
    ):
        _, outcome = _run_provider_failure(tmp_path, monkeypatch)

        assert outcome.exit_code is ExitCode.ERROR
        assert outcome.process_exit_code() == 1
        assert "Task completed successfully" not in caplog.text

    def test_failure_comment_names_provider_error_and_phase(
        self, tmp_path, monkeypatch
    ):
        _run_provider_failure(tmp_path, monkeypatch)

        journal = _journal(tmp_path)
        assert FAILURE_MARKER in journal
        assert "@olivier-norture" in journal
        assert "(agent phase)" in journal
        assert PROVIDER in journal
        assert MODEL in journal
        assert "404" in journal
        assert "won't help" in journal
        assert "did **not** advance" in journal
        assert "/nebuko resume" in journal

    def test_the_api_key_never_reaches_the_comment(self, tmp_path, monkeypatch):
        failure = ProviderFailure(
            f"Error code: 401 - invalid api key {API_KEY}",
            provider=PROVIDER,
            model=MODEL,
            requires_config_fix=True,
        )
        _run_provider_failure(tmp_path, monkeypatch, failure=failure)

        journal = _journal(tmp_path)
        assert API_KEY not in journal
        assert "invalid api key" in journal

    def test_secret_named_env_values_are_redacted_too(self, tmp_path, monkeypatch):
        """Not only ``*API_KEY*``: a ``*SECRET*`` value is scrubbed as well."""
        engine = _engine(tmp_path)
        engine.env["PROVIDER_CLIENT_SECRET"] = "s3cr3t-value-xyz"
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        engine.adapter.save_state(_prior_state())
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: (_ for _ in ()).throw(
                ProviderFailure(
                    "Error code: 401 - bad secret s3cr3t-value-xyz",
                    provider=PROVIDER,
                    model=MODEL,
                    requires_config_fix=True,
                )
            ),
        )

        engine.run()

        assert "s3cr3t-value-xyz" not in _journal(tmp_path)

    def test_engine_failure_state_matches_comment(self, tmp_path, monkeypatch):
        _run_provider_failure(tmp_path, monkeypatch)

        facts = _saved_state(tmp_path)["current_context"]["engine_failure"]
        assert facts["phase"] == "agent"
        assert facts["error"] == PROVIDER_ERROR
        assert facts["provider"] == PROVIDER
        assert facts["model"] == MODEL
        assert facts["requires_config_fix"] is True
        assert facts["ok"] is False
        # The same error and phase the comment names.
        journal = _journal(tmp_path)
        assert PROVIDER_ERROR in journal
        assert "(agent phase)" in journal

    def test_midrun_provider_failure_stops(self, tmp_path, monkeypatch):
        """A failure partway through a run takes the same terminal path."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        engine.adapter.save_state(_prior_state())

        calls = {"n": 0}

        def _boom(self, prompt):
            calls["n"] += 1
            raise _provider_failure()

        monkeypatch.setattr(Engine, "invoke_agent", _boom)
        outcome = engine.run()

        assert calls["n"] == 1
        assert outcome.exit_code is ExitCode.ERROR
        assert "validate" not in outcome.phases
        assert "route" not in outcome.phases
        assert PROVIDER_ERROR in _journal(tmp_path)

    def test_midrun_changes_pushed_and_reported(self, tmp_path, monkeypatch):
        """Work the agent already did is committed and pushed once, and said so."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        engine.adapter.save_state(_prior_state())
        monkeypatch.setattr(
            Engine, "_pending_worktree_paths", lambda self: (" M src/app.py",)
        )
        finalized: list[bool] = []

        def _finalize(self):
            finalized.append(True)
            return git_session.GuardResult(ok=True, detail="feature/x is clean and pushed")

        monkeypatch.setattr(Engine, "_git_session_finalize", _finalize)

        def _boom(self, prompt):
            raise _provider_failure()

        monkeypatch.setattr(Engine, "invoke_agent", _boom)
        outcome = engine.run()

        assert finalized == [True]
        assert outcome.exit_code is ExitCode.ERROR
        journal = _journal(tmp_path)
        assert "branch pushed: yes" in journal
        # Only the git finalize ran: no PR finalize, no publish, no route.
        assert "publish" not in outcome.phases
        assert "route" not in outcome.phases


class TestProviderFailureStaysResumable:
    def test_resume_reruns_step_without_retry_budget(self, tmp_path, monkeypatch):
        engine, _ = _run_provider_failure(tmp_path, monkeypatch)

        saved = engine.adapter.load_state()
        assert saved is not None
        assert saved.turn_outcome is not None
        assert saved.turn_outcome.incomplete_reason is IncompleteReason.FAILURE
        # Not an agent failure and not a soft retry: no budget was consumed.
        assert saved.soft_retry.attempts == 0

        result = _resolve_human(
            "/nebuko resume\n" + _state_block(saved), _prompt_base(tmp_path)
        )
        assert result["SKIP_AGENT"] == "false"
        assert result["WORKFLOW_STEP"] == "spec"


class TestRequestSpecificErrorKeepsItsHandling:
    def test_a_context_window_error_does_not_stop_at_the_agent_phase(
        self, tmp_path, monkeypatch
    ):
        """A deterministic 400 keeps today's FAILURE outcome, not the terminal path."""
        from openhands.sdk.llm.exceptions import LLMContextWindowExceedError

        from nebuko.engine.lib.agent_script.features.run_agent.run_failure import (
            classify_failure,
            is_provider_failure,
        )

        failure = classify_failure(LLMContextWindowExceedError("context too long"))
        assert not is_provider_failure(failure)

        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        engine.adapter.save_state(_prior_state())
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: {"answer": "## headline\n\npartial work"},
        )
        outcome = engine.run()

        # The ordinary pipeline still runs (the agent layer, not the engine,
        # decides that a request error is a plain FAILURE).
        assert "validate" in outcome.phases


def _prompt_base(tmp_path: Path) -> str:
    from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    for name in TASK_TO_PROMPT.values():
        (prompts / name).write_text(f"# Prompt for {name}")
    return str(prompts)


def _state_block(state: WorkflowState) -> str:
    from nebuko.engine.lib.workflow_state.features.to_json.to_json import state_to_json

    return (
        STATE_MARKER_START + "\n" + state_to_json(state) + "\n" + STATE_MARKER_END + "\n"
    )


def _resolve_human(body: str, prompt_base: str) -> dict:
    from nebuko.engine.lib.resolve_config import resolve_config

    return resolve_config(
        body,
        prompt_base,
        str(WORKFLOWS_DIR),
        is_bot=False,
        local_workflows_dir=str(LOCAL_WORKFLOWS_DIR),
    )
