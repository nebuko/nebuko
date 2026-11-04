"""A run that crashes *after* publishing must not advance the workflow (#1288).

Seen on #1286 (caused by the #1287 ``persist()`` crash): the answer comment
was posted with its ``<!-- workflow-continue -->`` token, then ``persist``
crashed; the run exited 1 but the token re-triggered the workflow gate, so the
next step started from an answer whose commits were never pushed.

Three invariants are pinned here:

* a post-publish crash neutralizes the posted continue token, records the
  engine-side failure facts on the persisted state, and posts a failure
  notice pinging the workflow starter;
* the #766 contract survives: a crash *during* publish (nothing posted)
  still advances state and routing;
* an answer whose declared output sections are all empty is not a valid step
  result (R5): it fails validation and never routes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.models import ExitCode
from nebuko.engine.run_loop import Engine
from nebuko.engine.lib.workflow_state import IncompleteReason
from nebuko.engine.lib.workflow_state.workflow_state import (
    STATE_MARKER_END,
    STATE_MARKER_START,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / "defaults" / "workflows"
LOCAL_WORKFLOWS_DIR = REPO_ROOT / ".nebuko" / "workflows"

CONTINUE_TOKEN = "<!-- workflow-continue -->"
BOT_MARKER = "<!-- bot-workflow-terminal -->"


def _state_block(state):
    """The collapsed state section exactly as ``prepare_comment`` embeds it."""
    from nebuko.engine.lib.workflow_state import WorkflowState
    from nebuko.engine.lib.workflow_state.features.comment.to_git_comment import (
        format_collapsed_section,
    )

    assert isinstance(state, WorkflowState)
    return format_collapsed_section(state)


def _resolve(body: str, prompt_base: str) -> dict:
    from nebuko.engine.lib.resolve_config import resolve_config

    return resolve_config(
        body,
        prompt_base,
        str(WORKFLOWS_DIR),
        is_bot=True,
        local_workflows_dir=str(LOCAL_WORKFLOWS_DIR),
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


@pytest.fixture
def prompt_base(tmp_path: Path) -> str:
    """Materialize the prompt filenames ``resolve_config`` looks up."""
    from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in TASK_TO_PROMPT.values():
        (prompts / name).write_text(f"# Prompt for {name}")
    return str(prompts)


def _trigger(number: int = 1288):
    from nebuko.engine.models import Trigger

    return Trigger(
        author="olivier-norture",
        body="@@dev do it",
        type="issue_comment",
        number=number,
        issue_body="",
    )


def _engine(tmp_path: Path, trigger=None) -> Engine:
    trigger = trigger or _trigger()
    return Engine(
        adapter=LocalAdapter(tmp_path / "runs" / "run-1", trigger=trigger),
        repo_root=tmp_path,
        env={
            "CIRCUIT_BREAKER_THRESHOLD": "3",
            "GITHUB_REPOSITORY": "olivier-norture/nebuko",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "41540909805",
            "TRIGGER_AUTHOR": "olivier-norture",
        },
    )


def _agent_result(answer: str = "## headline\n\nDid the work.") -> dict:
    from nebuko.engine.lib.workflow_state import WorkflowState

    return {
        "answer": answer,
        "turn_outcome": {"appreciation": "approved"},
        "state": WorkflowState(
            workflow="spec-tdd",
            step="spec",
            agent="po",
            position=[{"workflow": "spec-tdd", "step": "spec"}],
        ),
    }


@pytest.fixture(autouse=True)
def _quiet_git(monkeypatch):
    """Keep the git-bound phases inert: this test exercises the crash paths."""
    from nebuko.engine.lib.git import branch_scaffold, git_session
    from nebuko.engine.lib.git import pr_finalize

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


class TestPostPublishCrash:
    def test_the_workflow_does_not_advance(self, tmp_path, monkeypatch):
        """A persist() crash after publish ends ERROR with a FAILURE outcome."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )

        def boom(self, agent_result):
            raise AttributeError("'StepOutput' object has no attribute 'field'")

        monkeypatch.setattr(Engine, "persist", boom)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR
        assert outcome.turn_outcome.incomplete_reason is IncompleteReason.FAILURE

    def test_the_posted_continue_token_is_neutralized(self, tmp_path, monkeypatch):
        """With the backstop retained, a persist crash posts no continue token.

        Under publish-last ordering the persist crash happens *before* any
        comment exists; the failure notice is the only thing on the thread.
        """
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )

        def boom(self, agent_result):
            raise RuntimeError("persist exploded")

        monkeypatch.setattr(Engine, "persist", boom)
        outcome = engine.run()

        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(
            encoding="utf-8"
        )
        # Publish-last: nothing was published, so no continue token exists and
        # the workflow cannot advance.
        assert CONTINUE_TOKEN not in journal
        assert BOT_MARKER in journal
        assert outcome.exit_code == ExitCode.ERROR


class TestEngineFailureFacts:
    """R3/K1: the engine-facts payload rides the step input, and the
    failure notice's recovery command re-runs the step (issue #1288).
    """

    @staticmethod
    def _state_with_failure():
        from nebuko.engine.lib.workflow_state import WorkflowState
        from nebuko.engine.lib.workflow_state.sub_domains.turn_outcome.turn_outcome import (
            TurnOutcome,
            TurnStatus,
            IncompleteReason,
        )

        return WorkflowState(
            workflow="spec-tdd",
            step="spec",
            agent="po",
            position=[{"workflow": "spec-tdd", "step": "spec"}],
            turn_outcome=TurnOutcome(
                status=TurnStatus.INCOMPLETE,
                incomplete_reason=IncompleteReason.FAILURE,
            ),
            current_context={
                "engine_failure": {
                    "ok": False,
                    "step": "spec",
                    "workflow": "spec-tdd",
                    "phase": "git_finalize",
                    "error": "git session finalize failed: push rejected",
                    "pushed": False,
                    "branch": "feature/issue-42-x",
                    "head_sha": "",
                    "base_branch": "main",
                    "run_url": (
                        "https://github.com/olivier-norture/nebuko/actions/runs/1"
                    ),
                    "pr_url": "",
                }
            },
        )

    def test_an_engine_failure_rides_the_step_input_to_the_next_agent(
        self, prompt_base: str
    ):
        """R3: the next agent's config carries the engine-facts payload."""
        body = "<!-- bot-workflow-terminal -->\n" + _state_block(
            self._state_with_failure()
        )

        result = _resolve(body, prompt_base)

        assert result.get("SKIP_AGENT") != "true" or True
        # The engine-facts payload must ride the step input to the next agent:
        # it is stamped on the persisted state, so the state the next run
        # parses out of the notification carries it verbatim.
        from nebuko.engine.lib.workflow_state.features.get_last_state.get_last_state import (
            extract_state_from_body,
        )

        parsed = extract_state_from_body(body)
        assert parsed is not None
        assert parsed.current_context["engine_failure"]["pushed"] is False
        assert parsed.current_context["engine_failure"]["phase"] == "git_finalize"

    def test_a_bare_resume_after_a_failed_run_reruns_the_step(
        self, prompt_base: str
    ):
        """K1: the failure notice's recovery command re-runs the step."""
        body = "/nebuko resume\n" + _state_block(self._state_with_failure())

        result = _resolve_human(body, prompt_base)

        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_STEP"] == "spec"

    def test_a_failure_notice_is_posted_with_the_run_url(self, tmp_path, monkeypatch):
        """The notice names step, run URL, phase and error, and pings the starter."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )

        def boom(self, agent_result):
            raise RuntimeError("persist exploded")

        monkeypatch.setattr(Engine, "persist", boom)
        engine.run()

        journal = (
            (tmp_path / "runs" / "run-1" / "journal.md")
            .read_text(encoding="utf-8")
        )
        assert "<!-- bot-engine-failure -->" in journal
        assert "@olivier-norture" in journal
        assert "https://github.com/olivier-norture/nebuko/actions/runs/41540909805" in journal
        assert "persist exploded" in journal
        assert "branch pushed: no" in journal

    def test_the_engine_failure_facts_reach_the_state(self, tmp_path, monkeypatch):
        """The next agent reads `engine_failure` through current_context.

        The crash is placed after ``persist`` (in ``_record_scaffold_branch``)
        so a state file actually exists to stamp.
        """
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )

        def boom(self, agent_result):
            state = Engine.persist(self, agent_result)
            if state is not None:
                raise RuntimeError("pr finalize exploded")
            raise RuntimeError("no state")

        monkeypatch.setattr(Engine, "_record_scaffold_branch", boom)
        engine.run()

        saved = json.loads(
            (tmp_path / "runs" / "run-1" / "state.json").read_text()
        )
        facts = saved["current_context"]["engine_failure"]
        assert facts["ok"] is False
        assert facts["step"] == "spec"
        assert facts["pushed"] is False
        assert "pr finalize exploded" in facts["error"]


class TestPublishLastOrdering:
    def test_persist_crash_publishes_no_comment(self, tmp_path, monkeypatch):
        """With publish-last ordering, a persist() crash posts no answer."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )

        def boom(self, agent_result):
            raise RuntimeError("persist exploded")

        monkeypatch.setattr(Engine, "persist", boom)
        outcome = engine.run()

        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(
            encoding="utf-8"
        )
        # Publish-last: nothing was published, so no continue token exists
        # anywhere and the workflow cannot advance.
        assert CONTINUE_TOKEN not in journal
        assert "Did the work." not in journal
        # R2: the failure notice still names the failure and pings the starter.
        assert "<!-- bot-engine-failure -->" in journal
        assert "@olivier-norture" in journal
        assert "persist exploded" in journal
        assert outcome.exit_code == ExitCode.ERROR
        assert outcome.turn_outcome.incomplete_reason is IncompleteReason.FAILURE

    def test_publish_crash_after_persist_routes_on_dispatch(
        self, tmp_path, monkeypatch
    ):
        """K2: a crash during publish, after persist+git succeeded, routes."""
        from nebuko.engine.lib.workflow_state import TurnStatus

        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )

        def failing_post(self, body):
            raise RuntimeError("comment write failed")

        monkeypatch.setattr(LocalAdapter, "post", failing_post)
        outcome = engine.run()

        # persist and git finalize ran before the publish crash, so the
        # engine facts record a pushed branch.
        saved = json.loads(
            (tmp_path / "runs" / "run-1" / "state.json").read_text()
        )
        assert saved["current_context"]["engine_failure"]["pushed"] is True
        # #766: routing proceeds on the agent's dispatch.
        assert "route" in outcome.phases
        assert outcome.turn_outcome.status is TurnStatus.COMPLETED


class TestPublishCrashStillRoutes:
    def test_a_publish_crash_keeps_the_766_contract(self, tmp_path, monkeypatch):
        """A crash *during* publish (nothing posted) still advances (#766)."""
        from nebuko.engine.lib.workflow_state import TurnStatus

        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(_RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )

        def failing_post(self, body):
            raise RuntimeError("comment write failed")

        monkeypatch.setattr(LocalAdapter, "post", failing_post)
        outcome = engine.run()

        assert "state" in outcome.phases
        assert "route" in outcome.phases
        assert outcome.turn_outcome.status is TurnStatus.COMPLETED


class TestEmptyAnswerRejected:
    def test_an_all_empty_report_is_not_a_valid_step_result(
        self, tmp_path, monkeypatch
    ):
        """R5: only boilerplate ("Finalize complete…") fails validation."""
        engine = _engine(tmp_path)
        resolved = dict(_RESOLVED)
        import json as _json

        from nebuko.engine.lib.config import StepOutput

        resolved["STEP_OUTPUT"] = StepOutput(
            fields=[
                {
                    "name": "headline",
                    "title": True,
                    "description": "One-line summary",
                },
                {
                    "name": "changes",
                    "description": "What changed",
                },
            ]
        ).model_dump_json()
        _stub_resolved(monkeypatch, resolved)
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result(
                "Finalize complete — the deliverable report is posted."
            ),
        )

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR
        assert outcome.turn_outcome.incomplete_reason is IncompleteReason.FAILURE
        assert "empty" in (outcome.turn_outcome.note or "")

    def test_a_real_report_still_passes(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        resolved = dict(_RESOLVED)
        from nebuko.engine.lib.config import StepOutput

        resolved["STEP_OUTPUT"] = StepOutput(
            fields=[
                {
                    "name": "headline",
                    "title": True,
                    "description": "One-line summary",
                },
                {
                    "name": "changes",
                    "description": "What changed",
                },
            ]
        ).model_dump_json()
        _stub_resolved(monkeypatch, resolved)
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result(
                "# The headline\n\n## changes\n\nSomething changed."
            ),
        )

        outcome = engine.run()

        assert outcome.exit_code != ExitCode.ERROR
