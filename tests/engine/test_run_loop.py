"""Spec-scenario tests for the typed driver (issue #590, plan section 3.5).

The driver is the typed form of the 15-step ``run-task`` bash job: ten phases,
one ``[phase:<name>]`` log marker each, and an exit code that tells the host
whether to advance, stop for a human, or terminate.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

from nebuko.adapters.local import LocalAdapter
from config import AgentStartNotice, HumanActionNotice, brand
from nebuko.engine.models import ExitCode, RoutingDecision, RunOutcome, Trigger
from nebuko.engine.paths import repo_root
from workflow_state.workflow_state import WorkflowState
from nebuko.engine.run_loop import (
    PHASES,
    Engine,
    _backfill_workflow_identity,
    _ensure_child_identity,
    _exit_code_for,
    _stamp_llm_session_id,
    _TRIGGER_KEY,
    main,
    resolved_env_overlay,
)
from workflow_state import extract_state_from_body
from workflow_state.step_output_record import StepOutputRecord

BOT_AGENT_MARKER = "<!-- bot-agent-dev -->"
WORKFLOW_STATE_MARKER = "<!-- WORKFLOW_STATE_START -->"

# The GitHub host surface the removed bash acknowledgment step read (issue #720).
RUN_ID = "36979038271"
RUN_URL = f"https://github.com/olivier-norture/git-flow-ai/actions/runs/{RUN_ID}"
RUN_ENV = {
    "CIRCUIT_BREAKER_THRESHOLD": "3",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "olivier-norture/git-flow-ai",
    "GITHUB_RUN_ID": RUN_ID,
}
ACK_LINE = f"{brand().logo_mark()} **dev agent started** — [View workflow run logs]({RUN_URL})"


def _trigger(body: str = "@@dev fix it", number: int = 590, issue_body: str = "") -> Trigger:
    return Trigger(
        author="olivier-norture",
        body=body,
        type="issue_comment",
        number=number,
        issue_body=issue_body,
    )


def _engine(tmp_path, trigger=None, *, name: str = "run-1", env=None) -> Engine:
    adapter = LocalAdapter(tmp_path / "runs" / name, trigger=trigger or _trigger())
    # Production runs with ``dict(os.environ)``, where GitHub Actions always sets
    # ``GITHUB_REPOSITORY``; the fixture mirrors that so the child-identity seed
    # (issue #739) resolves for every run-flow test.
    base_env = {
        "CIRCUIT_BREAKER_THRESHOLD": "3",
        "GITHUB_REPOSITORY": "olivier-norture/git-flow-ai",
    }
    base_env.update(env or {})
    return Engine(adapter=adapter, repo_root=repo_root(), env=base_env)


def _agent_result(answer: str, dispatch: dict | None = None) -> dict:
    return {"answer": answer, "dispatch": dispatch if dispatch is not None else {"status": "advance"}}


def _patch_resolver(monkeypatch, resolver) -> None:
    """Replace the resolver facade with ``resolver`` for a unit test.

    ``resolve_config.resolve_config`` is the facade function, imported by
    ``Engine.resolve`` from its submodule, so the patch targets the submodule
    object (the package re-exports the function under the same name).
    """
    import sys

    import resolve_config  # noqa: F401 - ensures the submodule is imported

    monkeypatch.setattr(
        sys.modules["resolve_config.resolve_config"], "resolve_config", resolver
    )


def _stub_resolved(monkeypatch, resolved: dict) -> None:
    """Pin the resolver's output for a guard-phase unit test."""
    _patch_resolver(monkeypatch, lambda **_: resolved)


# The captured soft-kill state from issue #793 (step=implement, agent=dev,
# dispatch.status=soft_kill, last_run_status=gracefully_timed_out).
SOFT_KILL_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "src/nebuko/engine/lib/fixtures/workflow_state_issue793_soft_kill.json"
)


def _soft_kill_comment_body() -> str:
    """The engine's own soft-kill conclusion comment (trigger for run N+1)."""
    state = json.loads(SOFT_KILL_FIXTURE.read_text(encoding="utf-8"))
    return "\n".join(
        [
            "<!-- bot-workflow-terminal -->",
            BOT_AGENT_MARKER,
            "<!-- workflow-continue -->",
            "<details>",
            "<summary>Workflow State</summary>",
            "```json",
            WORKFLOW_STATE_MARKER,
            json.dumps(state),
            "<!-- WORKFLOW_STATE_END -->",
            "```",
            "</details>",
        ]
    )


class TestPhaseVocabulary:
    def test_phases_are_the_ten_documented_phases_in_order(self):
        assert PHASES == (
            "normalize",
            "resolve_config",
            "guards",
            "action",
            "prompt",
            "agent",
            "validate",
            "publish",
            "state",
            "route",
        )


class TestNormalizePhase:
    def test_returns_the_adapter_trigger(self, tmp_path):
        engine = _engine(tmp_path)

        assert engine.normalize() == _trigger()


class TestGuardsPhase:
    def test_accepts_a_plain_human_trigger(self, tmp_path):
        engine = _engine(tmp_path)

        verdict = engine.guard(_trigger(), [])

        assert str(verdict["SKIP_AGENT"]).lower() == "false"

    def test_skips_a_same_agent_loop(self, tmp_path, monkeypatch):
        """The bash gate skips when the caller marker equals the resolver's TASK."""
        trigger = _trigger(body=f"<!-- bot-workflow-terminal -->\n{BOT_AGENT_MARKER}\n@@dev continue")
        engine = _engine(tmp_path, trigger)
        # The bash compares ``CALLER_AGENT`` with ``TASK`` as the resolver
        # exported it; stub that seam, not the guard's own comparison.
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "false", "TASK": "dev"})

        verdict = engine.guard(trigger, [])

        assert str(verdict["SKIP_AGENT"]).lower() == "true"

    def test_a_soft_retry_bypasses_the_same_agent_loop_guard(self, tmp_path, monkeypatch):
        """Issue #793: an engine soft-retry re-runs the same agent by design.

        Without the bypass the guard classifies the identical caller/position as
        a self-loop and skips the retry, so the soft-killed step never resumes.
        The ``MAX_SOFT_RETRIES`` budget bounds the chain instead.
        """
        trigger = _trigger(body=f"<!-- bot-workflow-terminal -->\n{BOT_AGENT_MARKER}\n{_soft_kill_comment_body()}")
        engine = _engine(tmp_path, trigger)
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "false",
                "TASK": "dev",
                "WORKFLOW_POSITION_PATH": "default/implement",
                "WORKFLOW_STEP": "implement",
                "SOFT_RETRY_ATTEMPT": "true",
            },
        )

        verdict = engine.guard(trigger, [])

        assert str(verdict["SKIP_AGENT"]).lower() == "false"

    def test_makes_no_comparison_without_a_resolver_task(self, tmp_path, monkeypatch):
        """A caller marker without a resolver ``TASK`` skips the comparison.

        This mirrors the legacy replay in the parity net (``_legacy_guard``):
        the bash leaves ``SKIP_AGENT`` at the resolver's value, and the driver
        only ever reaches ``guard`` when that value is not ``true`` (``run``
        short-circuits on the resolver's skip, exactly as the job does).
        """
        trigger = _trigger(body=f"<!-- bot-workflow-terminal -->\n{BOT_AGENT_MARKER}\n@@dev continue")
        engine = _engine(tmp_path, trigger)
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "false", "TASK": ""})

        verdict = engine.guard(trigger, [])

        assert str(verdict["SKIP_AGENT"]).lower() == "false"

    def test_uses_the_injected_history_for_the_circuit_breaker(self, tmp_path):
        from nebuko.engine.models import CommentEntry

        engine = _engine(tmp_path)
        history = [
            CommentEntry(author="bot", body=f"{BOT_AGENT_MARKER}\ncall {index}", created_at="2026-10-01T07:47:47Z")
            for index in range(6)
        ]

        verdict = engine.guard(_trigger(), history)

        assert str(verdict["CIRCUIT_BREAKER_TRIPPED"]).lower() == "true"


def _bot_marker_comments(count: int) -> list:
    from nebuko.engine.models import CommentEntry

    return [
        CommentEntry(
            author="bot",
            body=f"{BOT_AGENT_MARKER}\ncall {index}",
            created_at="2026-10-01T07:47:47Z",
        )
        for index in range(count)
    ]


def _circuit_breaker_state_comment(limit: int):
    from nebuko.engine.models import CommentEntry
    from workflow_state import CircuitBreakerState, to_git_comment

    state = WorkflowState(circuit_breaker=CircuitBreakerState(limit=limit))
    return CommentEntry(
        author="bot", body=to_git_comment(state), created_at="2026-10-01T07:47:47Z"
    )


class TestCircuitBreakerLimitOverride:
    """Per-issue breaker override (issue #839): persisted state wins over env.

    The breaker mechanism recounts markers every run and compares against the
    *effective* threshold, so raising the override above the current call count
    un-trips the issue with no separate reset verb.
    """

    def test_absent_state_uses_the_env_threshold(self, tmp_path):
        engine = _engine(tmp_path)  # CIRCUIT_BREAKER_THRESHOLD=3

        verdict = engine.guard(_trigger(), _bot_marker_comments(6))

        assert str(verdict["CIRCUIT_BREAKER_TRIPPED"]).lower() == "true"

    def test_state_override_raises_the_threshold(self, tmp_path):
        engine = _engine(tmp_path)
        history = _bot_marker_comments(6) + [_circuit_breaker_state_comment(10)]

        verdict = engine.guard(_trigger(), history)

        assert str(verdict["CIRCUIT_BREAKER_TRIPPED"]).lower() == "false"

    def test_state_override_can_tighten_the_threshold(self, tmp_path):
        engine = _engine(tmp_path)
        history = _bot_marker_comments(4) + [_circuit_breaker_state_comment(2)]

        verdict = engine.guard(_trigger(), history)

        assert str(verdict["CIRCUIT_BREAKER_TRIPPED"]).lower() == "true"

    def test_raising_the_override_un_trips_a_tripped_issue(self, tmp_path):
        engine = _engine(tmp_path)
        history = _bot_marker_comments(6)

        assert (
            str(engine.guard(_trigger(), history)["CIRCUIT_BREAKER_TRIPPED"]).lower()
            == "true"
        )

        unlocked = history + [_circuit_breaker_state_comment(10)]
        assert (
            str(engine.guard(_trigger(), unlocked)["CIRCUIT_BREAKER_TRIPPED"]).lower()
            == "false"
        )


class TestSoftKillRetry:
    """End-to-end restart of a soft-killed step (issue #793).

    The soft-kill conclusion comment is the engine's own trigger: the next run
    must clear the guards and reach the agent instead of short-circuiting on
    the same-agent loop guard.
    """

    def test_the_soft_kill_comment_reaches_the_agent_phase(self, tmp_path, monkeypatch):
        body = _soft_kill_comment_body()
        trigger = _trigger(body=body, number=793)
        engine = _engine(tmp_path, trigger)
        invoked: list[str] = []
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: invoked.append(prompt) or _agent_result("done")
        )

        outcome = engine.run()

        assert invoked, "the engine retried nothing; the soft-killed step never resumed"
        assert "agent" in outcome.phases

    def test_the_resolver_marks_the_retry_with_the_soft_retry_attempt(self, tmp_path):
        """The real resolver must classify the fixture comment as a retry."""
        engine = _engine(tmp_path, _trigger(body=_soft_kill_comment_body(), number=793))

        resolved = engine.resolve(engine.normalize())

        assert str(resolved.get("SKIP_AGENT")).lower() == "false"
        assert str(resolved.get("SOFT_RETRY_ATTEMPT")).lower() == "true"

    def test_exhausted_budget_posts_a_human_halt_notice(self, tmp_path, monkeypatch):
        """The exhausted retry halts and pings a human instead of going silent."""
        engine = _engine(tmp_path, _trigger(body=_soft_kill_comment_body(), number=793))
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "SOFT_RETRY_EXHAUSTED": "true",
                "REASON": (
                    "Soft time-kill: step 'implement' exhausted its retry budget "
                    "(2/2); halting the workflow for a human to resume with /nebuko resume."
                ),
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL
        assert "agent" not in outcome.phases
        assert outcome.posted_body is not None
        assert outcome.posted_body.splitlines()[0] == "<!-- bot-workflow-terminal -->"
        assert "/nebuko resume" in outcome.posted_body
        # No continue token: the automated chain must stop so a human takes over.
        assert "<!-- workflow-continue -->" not in outcome.posted_body
        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert "/nebuko resume" in journal

    def test_a_routine_resolver_skip_stays_silent(self, tmp_path, monkeypatch):
        """Only the exhausted soft-kill skip posts; an ordinary skip does not."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "true", "REASON": "nothing to do"})
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL
        assert outcome.posted_body is None
        journal = tmp_path / "runs" / "run-1" / "journal.md"
        assert not journal.exists() or journal.read_text(encoding="utf-8") == ""


class TestCommandErrorNotice:
    """A malformed / unknown human slash command posts feedback (issue #804)."""

    def test_malformed_human_command_posts_a_usage_notice(self, tmp_path, monkeypatch):
        engine = _engine(
            tmp_path, _trigger(body="/nebuko run workflow --default", number=804)
        )
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "COMMAND_ERROR": "true",
                "REASON": "/nebuko run workflow --name <workflow> [--step <step>]",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL
        assert "agent" not in outcome.phases
        assert outcome.posted_body is not None
        assert outcome.posted_body.splitlines()[0] == "<!-- bot-workflow-terminal -->"
        assert "@olivier-norture" in outcome.posted_body
        assert "/nebuko run workflow --name <workflow>" in outcome.posted_body
        # No continue token: an error notice must not re-trigger the chain.
        assert "<!-- workflow-continue -->" not in outcome.posted_body

    def test_unknown_slash_command_posts_notice(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, _trigger(body="/foo bar", number=804))
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "COMMAND_ERROR": "true",
                "REASON": "unrecognized / command; see the Human Commands table",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL
        assert outcome.posted_body is not None
        assert "unrecognized / command" in outcome.posted_body

    def test_bot_authored_command_error_stays_silent(self, tmp_path, monkeypatch):
        """The engine's own marker-led notices never re-enter the path (loop safety)."""
        bot_body = "<!-- bot-workflow-terminal -->\n/nebuko run workflow --default"
        engine = _engine(tmp_path, _trigger(body=bot_body, number=804))
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "COMMAND_ERROR": "true",
                "REASON": "/nebuko run workflow --name <workflow>",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL
        assert outcome.posted_body is None

    def test_the_real_resolver_flags_a_malformed_command(self, tmp_path):
        """End-to-end: the real resolver carries usage + COMMAND_ERROR (issue #804)."""
        engine = _engine(
            tmp_path, _trigger(body="/nebuko run workflow --default", number=804)
        )

        resolved = engine.resolve(engine.normalize())

        assert str(resolved.get("SKIP_AGENT")).lower() == "true"
        assert str(resolved.get("COMMAND_ERROR")).lower() == "true"
        assert "/nebuko run workflow --name" in resolved.get("REASON", "")

    def test_the_real_resolver_flags_an_unknown_slash_command(self, tmp_path):
        engine = _engine(tmp_path, _trigger(body="/foo bar", number=804))

        resolved = engine.resolve(engine.normalize())

        assert str(resolved.get("SKIP_AGENT")).lower() == "true"
        assert str(resolved.get("COMMAND_ERROR")).lower() == "true"
        assert "unrecognized" in resolved.get("REASON", "")

    def test_the_real_resolver_keeps_auto_routing_skips_silent(self, tmp_path):
        """A non-command body must not be flagged as a command error."""
        engine = _engine(tmp_path, _trigger(body="just some prose", number=804))

        resolved = engine.resolve(engine.normalize())

        assert str(resolved.get("SKIP_AGENT")).lower() == "true"
        assert not resolved.get("COMMAND_ERROR")


class TestDeterministicActionPhase:
    """``action`` executes the resolver's agent-free step (issue #724)."""

    def test_clean_comments_runs_the_step_and_exits_zero(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, _trigger(body="/nebuko clean comments", number=724))
        invoked: list[str] = []
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: invoked.append(prompt))
        # Seed the local journal so the deletion has something to remove.
        engine.adapter.post("historical comment")

        outcome = engine.run()

        assert invoked == []
        assert "action" in outcome.phases
        assert "agent" not in outcome.phases
        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.routing.status == "terminate"
        assert outcome.posted_body == "deleted 1 comments on issue 724"
        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert journal == ""

    def test_the_step_is_executed_before_the_skip_verdict(self, tmp_path, monkeypatch):
        """The delete contract also sets SKIP_AGENT, but the step still runs."""
        engine = _engine(tmp_path)
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "DELETE_COMMENTS": "true",
                "DELETE_COMMENTS_ISSUE": "900",
            },
        )
        deleted: list[tuple[str, str]] = []

        def record(self, issue, from_comment=""):
            deleted.append((issue, from_comment))
            return 7

        monkeypatch.setattr(LocalAdapter, "delete_issue_comments", record)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert deleted == [("900", "")]
        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.posted_body == "deleted 7 comments on issue 900"

    def test_a_pure_resolver_skip_still_terminates(self, tmp_path, monkeypatch):
        """SKIP_AGENT without a step flag keeps the terminal-20 contract."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "true", "REASON": "nothing to do"})
        invoked: list[str] = []
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: invoked.append(prompt))

        outcome = engine.run()

        assert invoked == []
        assert "agent" not in outcome.phases
        assert outcome.exit_code == ExitCode.TERMINAL


class TestScriptStepComments:
    """A ``run_script`` step joins the comment lifecycle (issue #885).

    It posts a start notice (no continue token) and a state-carrying completion
    comment whose body is registered as the step's output, so ``inputs: [...]``
    wiring and the workflow-continue gate both work exactly as for an agent
    step.
    """

    def _stub_script(
        self, monkeypatch, script_args: str = '-c "print(\'hello\')"', **extra
    ) -> None:
        resolved = {
            "SKIP_AGENT": "true",
            "RUN_SCRIPT": "true",
            "SCRIPT_PATH": sys.executable,
            "SCRIPT_ARGS": script_args,
            "WORKFLOW_STEP": "run_tests",
            "WORKFLOW_NAME": "default",
        }
        resolved.update(extra)
        _stub_resolved(monkeypatch, resolved)

    def test_posts_a_start_notice_and_a_state_carrying_completion(
        self, tmp_path, monkeypatch
    ):
        engine = _engine(tmp_path)
        self._stub_script(monkeypatch)
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran")
        )

        outcome = engine.run()

        entries = engine.adapter.load_history()
        assert len(entries) == 2

        start = entries[0].body
        assert "<!-- bot-command-run_tests -->" in start
        assert "**run_tests command started**" in start
        assert "<!-- workflow-continue -->" not in start
        assert WORKFLOW_STATE_MARKER not in start

        completion = outcome.posted_body
        assert completion.startswith("<!-- bot-workflow-terminal -->")
        assert "<!-- bot-command-run_tests -->" in completion
        assert "<!-- workflow-continue -->" in completion
        assert "**run_tests command succeeded**" in completion
        assert "<summary>command output</summary>" in completion
        assert "hello" in completion

        state = extract_state_from_body(completion)
        assert state is not None
        assert state.step == "run_tests"
        assert state.dispatch is not None
        assert state.dispatch.status == "advance"
        assert state.dispatch.branch == "pass"

    def test_registers_the_completion_comment_as_the_step_output(
        self, tmp_path, monkeypatch
    ):
        engine = _engine(tmp_path)
        self._stub_script(monkeypatch)

        engine.run()

        saved = engine.adapter.load_state()
        records = {
            record.step: record
            for record in StepOutputRecord.load_all(saved.current_context)
        }
        assert "run_tests" in records
        # The completion comment is the journal's second entry.
        assert records["run_tests"].comment_id == "2"

    def test_the_output_is_addressable_by_downstream_inputs(
        self, tmp_path, monkeypatch
    ):
        engine = _engine(tmp_path)
        self._stub_script(monkeypatch)

        engine.run()

        scope = engine._step_output_scope(engine.adapter.load_history())
        assert "run_tests" in scope
        assert "hello" in scope["run_tests"]
        # The bot-command tag is stripped from the addressable output, like the
        # bot-agent tag is for agent steps.
        assert "bot-command-run_tests" not in scope["run_tests"]

    def test_a_failing_script_reports_failed_and_branches_fail(
        self, tmp_path, monkeypatch
    ):
        engine = _engine(tmp_path)
        self._stub_script(
            monkeypatch, script_args='-c "import sys; sys.exit(2)"'
        )

        outcome = engine.run()

        assert "**run_tests command failed**" in outcome.posted_body
        assert "exit code 2" in outcome.posted_body
        state = extract_state_from_body(outcome.posted_body)
        assert state.dispatch.branch == "fail"

    def test_the_comment_never_echoes_the_command_line(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        self._stub_script(monkeypatch, script_args='-c "import sys; sys.exit(0)"')

        outcome = engine.run()

        assert sys.executable not in outcome.posted_body
        assert "import sys" not in outcome.posted_body

    def test_script_comments_do_not_count_as_agent_calls(self, tmp_path, monkeypatch):
        from circuit_breaker import count_bot_markers

        engine = _engine(tmp_path)
        self._stub_script(monkeypatch)

        engine.run()

        assert count_bot_markers(engine.adapter.load_history()) == 0


class TestBenignNoNextAgentExit:
    """Issue #853: a workflow with no next agent stops green, not with exit 20."""

    def test_no_next_agent_exits_zero_and_records_the_reason(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        engine.adapter.save_state(
            WorkflowState.model_validate(
                {
                    "version": 7,
                    "workflow": "default",
                    "step": "review",
                    "agent": "review",
                    "current_context": {"last_run_status": "success"},
                }
            )
        )
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "REASON": "No valid task found",
                "SKIP_KIND": "no_next_agent",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.posted_body is None
        assert "agent" not in outcome.phases
        assert outcome.state is not None
        # The reason is recorded in the state alongside the existing context.
        assert outcome.state.current_context["terminal_reason"] == "No valid task found"
        assert outcome.state.current_context["last_run_status"] == "success"

    def test_a_terminal_step_skip_exits_zero(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "REASON": "Terminal step 'review' has no next in workflow 'default'",
                "SKIP_KIND": "terminal",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.posted_body is None

    def test_a_config_error_skip_still_exits_terminal(self, tmp_path, monkeypatch):
        """Regression guard: a real config failure keeps the terminal exit (20)."""
        engine = _engine(tmp_path)
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "REASON": "Resolution failed: workflow file 'nope.yaml' not found",
                "SKIP_KIND": "config_error",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL
        assert outcome.posted_body is None

    def test_an_unclassified_skip_keeps_the_terminal_contract(self, tmp_path, monkeypatch):
        """Fail-safe: a reason without a kind must never be softened to exit 0."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "true", "REASON": "mystery"})
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL

    def test_the_real_resolver_marks_an_empty_task_as_no_next_agent(self, tmp_path):
        """End-to-end: the resolver classifies its no-task fallback as benign."""
        engine = _engine(tmp_path, _trigger(body="just some prose", number=853))

        resolved = engine.resolve(engine.normalize())

        assert str(resolved.get("SKIP_AGENT")).lower() == "true"
        assert resolved.get("SKIP_KIND") == "no_next_agent"

    def test_a_mis_typed_kind_never_swallows_a_soft_kill_ping(self, tmp_path, monkeypatch):
        """Regression guard (issue #853): a notice-bearing skip outranks a benign kind.

        If the classifier ever mis-labelled an exhausted soft-kill halt benign,
        gating the benign branch on the notice flags must still keep the human
        ping and the failing exit (not exit 0 with no notice).
        """
        engine = _engine(tmp_path, _trigger(body=_soft_kill_comment_body(), number=793))
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "SOFT_RETRY_EXHAUSTED": "true",
                "SKIP_KIND": "terminal",
                "REASON": (
                    "Soft time-kill: step 'implement' exhausted its retry budget "
                    "(2/2); halting the workflow for a human to resume with /nebuko resume."
                ),
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL
        assert outcome.posted_body is not None
        assert "/nebuko resume" in outcome.posted_body

    def test_a_mis_typed_kind_never_swallows_a_command_error(self, tmp_path, monkeypatch):
        """Regression guard (issue #853): a benign kind must not hide a command error."""
        engine = _engine(
            tmp_path, _trigger(body="/nebuko run workflow --default", number=804)
        )
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "COMMAND_ERROR": "true",
                "SKIP_KIND": "terminal",
                "REASON": "/nebuko run workflow --name <workflow> [--step <step>]",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.TERMINAL
        assert outcome.posted_body is not None
        assert "@olivier-norture" in outcome.posted_body

    def test_a_benign_skip_without_a_previous_state_annotates_nothing(
        self, tmp_path, monkeypatch
    ):
        """``_record_terminal_skip`` leaves a missing prior state alone (returns None)."""
        engine = _engine(tmp_path)
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "REASON": "No valid task found",
                "SKIP_KIND": "no_next_agent",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.state is None


class TestRoutePhase:
    def test_maps_the_dispatch_payload(self, tmp_path):
        engine = _engine(tmp_path)

        decision = engine.route({"dispatch": {"status": "advance", "agent": "context", "complexity": "high"}})

        assert isinstance(decision, RoutingDecision)
        assert (decision.status, decision.agent, decision.complexity) == ("advance", "context", "high")

    def test_maps_a_human_gate_hold(self, tmp_path):
        engine = _engine(tmp_path)

        decision = engine.route({"dispatch": {"status": "hold", "reason": "await approval"}})

        assert decision.status == "hold"

    def test_maps_a_soft_kill_to_advance(self, tmp_path):
        """Issue #793: ``soft_kill`` is a live status, not a silent fall-through.

        The run exits ``ADVANCE`` (its own comment re-triggers the retry)
        rather than relying on the ``.get`` default in ``_exit_code_for``.
        """
        engine = _engine(tmp_path)

        decision = engine.route({"dispatch": {"status": "soft_kill"}})

        assert decision.status == "soft_kill"
        assert _exit_code_for(decision) == ExitCode.ADVANCE

    def test_engine_fallback_advance_exits_advance(self, tmp_path):
        """Issue #864: a decision-less callee with a return address exits
        ``ADVANCE`` (0) so its callback return is honored, not a dead-end hold.
        """
        engine = _engine(tmp_path)

        decision = engine.route(
            {
                "dispatch": {
                    "status": "advance",
                    "origin": "engine_fallback",
                    "reason": "no-action default: agent emitted no decision; "
                    "returning to caller 'architect'",
                }
            }
        )

        assert decision.status == "advance"
        assert _exit_code_for(decision) == ExitCode.ADVANCE

    def test_engine_fallback_hold_exits_hold(self, tmp_path):
        """A rudderless decision-less run still exits ``HOLD`` (10) (#765)."""
        engine = _engine(tmp_path)

        decision = engine.route(
            {
                "dispatch": {
                    "status": "hold",
                    "origin": "engine_fallback",
                    "reason": "no-action default: agent emitted no decision",
                }
            }
        )

        assert decision.status == "hold"
        assert _exit_code_for(decision) == ExitCode.HOLD


class TestHumanActionNotice:
    """The engine owns the human ping (issue #795).

    An agent reports its result and never mentions a human; the engine
    classifies the finished run and posts the single canonical notice.
    """

    def test_advance_out_of_a_terminal_step_is_workflow_complete(self, tmp_path):
        engine = _engine(tmp_path)

        notice = engine.human_action_notice(
            routing=RoutingDecision(status="advance"),
            agent_ran=True,
            state=None,
            resolved={"WORKFLOW_NAME": "default", "WORKFLOW_STEP": "review"},
        )

        assert notice is not None
        assert notice.kind == "workflow_complete"

    def test_advance_out_of_a_mid_workflow_step_pings_nobody(self, tmp_path):
        engine = _engine(tmp_path)

        notice = engine.human_action_notice(
            routing=RoutingDecision(status="advance"),
            agent_ran=True,
            state=None,
            resolved={"WORKFLOW_NAME": "default", "WORKFLOW_STEP": "plan"},
        )

        assert notice is None

    def test_a_hold_escalates_to_an_approval_or_input_notice(self, tmp_path):
        engine = _engine(tmp_path)
        resolved = {"WORKFLOW_NAME": "default", "WORKFLOW_STEP": "plan"}

        gate = engine.human_action_notice(
            routing=RoutingDecision(status="hold", reason="human_gate:plan"),
            agent_ran=True,
            state=None,
            resolved=resolved,
        )
        plain = engine.human_action_notice(
            routing=RoutingDecision(status="hold", reason="awaiting feedback"),
            agent_ran=True,
            state=None,
            resolved=resolved,
        )

        assert gate is not None and gate.kind == "awaiting_approval"
        assert plain is not None and plain.kind == "awaiting_input"

    def test_a_run_that_skipped_the_agent_never_pings(self, tmp_path):
        engine = _engine(tmp_path)

        notice = engine.human_action_notice(
            routing=RoutingDecision(status="advance"),
            agent_ran=False,
            state=None,
            resolved={"WORKFLOW_NAME": "default", "WORKFLOW_STEP": "review"},
        )

        assert notice is None

    def test_notify_human_action_posts_the_canonical_body(self, tmp_path):
        engine = _engine(tmp_path)
        notice = HumanActionNotice(
            kind="awaiting_approval",
            target="olivier-norture",
            step="plan",
            workflow="default",
            reason="human_gate:plan",
        )

        engine.notify_human_action(notice)

        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert "<!-- bot-human-action -->" in journal
        assert "@olivier-norture" in journal

    def test_notify_human_action_never_aborts_the_run(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        notice = HumanActionNotice(kind="awaiting_input", target="olivier-norture")

        def boom(self, body):
            raise RuntimeError("network down")

        monkeypatch.setattr(LocalAdapter, "post", boom)

        engine.notify_human_action(notice)  # must not raise

    def test_a_human_gate_hold_posts_the_engine_owned_ping(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result(
                "proposal", {"status": "hold", "reason": "human_gate:plan"}
            ),
        )

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.HOLD
        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert "<!-- bot-human-action -->" in journal
        assert "@olivier-norture" in journal
        assert "waiting for your approval" in journal

    def test_a_guard_skip_never_posts_a_human_notice(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "true", "REASON": "nothing to do"})
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        engine.run()

        journal = tmp_path / "runs" / "run-1" / "journal.md"
        assert not journal.exists() or "<!-- bot-human-action -->" not in journal.read_text(
            encoding="utf-8"
        )

    def test_the_engine_publish_neutralizes_agent_human_pings(self, tmp_path, monkeypatch):
        """An agent-composed human mention is inert by the time it is posted."""
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result("@olivier-norture ready for review"),
        )

        outcome = engine.run()

        assert outcome.posted_body is not None
        assert "@olivier-norture" not in outcome.posted_body
        assert "@\u200bolivier-norture" in outcome.posted_body


class TestHumanActionRecipient:
    """The engine pings the workflow *starter*, not whoever triggered last (#795)."""

    @staticmethod
    def _state(*, started_by=None, issue_author=None):
        data = {
            "version": 3,
            "workflow": "default",
            "step": "plan",
            "agent": "architect",
            "history": [{"step": "plan", "agent": "architect", "summary": "ok"}],
        }
        if started_by is not None:
            data["started_by"] = started_by
        if issue_author is not None:
            data["issue"] = {
                "number": 795,
                "url": "https://example.invalid/issues/795",
                "title": "engine pings the human",
                "author": issue_author,
                "repo": "nebuko/nebuko",
            }
        return WorkflowState.model_validate(data)

    def test_prefers_the_recorded_starter(self, tmp_path):
        engine = _engine(tmp_path, env={"TRIGGER_AUTHOR": "someone-else"})

        author = engine._trigger_author(
            self._state(started_by="olivier-norture", issue_author="issue-author")
        )

        assert author == "olivier-norture"

    def test_falls_back_to_the_issue_author_for_a_stale_state(self, tmp_path):
        """A pre-#795 state has no ``started_by``; the issue author still works."""
        engine = _engine(tmp_path, env={"TRIGGER_AUTHOR": "someone-else"})

        author = engine._trigger_author(self._state(issue_author="issue-author"))

        assert author == "issue-author"

    def test_falls_back_to_the_trigger_author_without_state(self, tmp_path):
        engine = _engine(tmp_path, env={"TRIGGER_AUTHOR": "triggerer"})

        assert engine._trigger_author(None) == "triggerer"

    def test_falls_back_to_the_adapter_trigger(self, tmp_path):
        engine = _engine(tmp_path)

        assert engine._trigger_author(None) == "olivier-norture"


class TestHumanActionSinglePing:
    """A stop pings a human exactly once (issue #795)."""

    def test_a_gated_hold_posts_exactly_one_notice(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result(
                "proposal", {"status": "hold", "reason": "human_gate:plan"}
            ),
        )

        engine.run()

        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert journal.count("<!-- bot-human-action -->") == 1

    def test_the_exhausted_soft_kill_halt_does_not_double_ping(self, tmp_path, monkeypatch):
        """The #793 halt ping and the #795 notice must never both fire."""
        engine = _engine(
            tmp_path,
            _trigger(body=_soft_kill_comment_body(), number=793),
            env={"TRIGGER_AUTHOR": "someone-else"},
        )
        # The halt body must ping the recorded workflow starter, not the raw
        # (here: different) trigger author — the #795 recipient unification.
        engine.adapter.save_state(
            WorkflowState.model_validate(
                {
                    "version": 7,
                    "workflow": "default",
                    "step": "implement",
                    "agent": "dev",
                    "history": [
                        {"step": "implement", "agent": "dev", "summary": "ok"}
                    ],
                    "started_by": "workflow-starter",
                }
            )
        )
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "SOFT_RETRY_EXHAUSTED": "true",
                "REASON": "Soft time-kill: budget exhausted; resume with /nebuko resume.",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.posted_body is not None
        assert "/nebuko resume" in outcome.posted_body
        assert "@workflow-starter" in outcome.posted_body
        assert "@someone-else" not in outcome.posted_body
        # The human-aware halt body is the only ping: no second engine notice.
        assert "<!-- bot-human-action -->" not in outcome.posted_body

    def test_the_follow_up_terminal_skip_run_posts_no_notice(self, tmp_path, monkeypatch):
        """Only the *completing* run announces the end of the workflow (#795)."""
        engine = _engine(tmp_path)
        _stub_resolved(
            monkeypatch,
            {
                "SKIP_AGENT": "true",
                "REASON": "Terminal step 'review' has no next in workflow 'default'",
            },
        )
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: pytest.fail("agent ran"))

        outcome = engine.run()

        assert outcome.posted_body is None


class TestRunLoop:
    def test_emits_every_phase_marker_in_order(self, tmp_path, monkeypatch, caplog):
        engine = _engine(tmp_path)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        with caplog.at_level("INFO"):
            outcome = engine.run()

        assert isinstance(outcome, RunOutcome)
        assert tuple(outcome.phases) == PHASES
        for phase in PHASES:
            assert f"[phase:{phase}]" in caplog.text

    def test_advances_on_an_advance_dispatch(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.routing.status == "advance"

    def test_holds_for_a_human_gate(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result("proposal", {"status": "hold", "reason": "await approval"}),
        )

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.HOLD
        assert outcome.routing.status == "hold"

    def test_publishes_the_agent_answer(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("*Dev answer*"))

        outcome = engine.run()

        assert outcome.posted_body is not None
        assert "*Dev answer*" in outcome.posted_body
        assert (tmp_path / "runs" / "run-1" / "journal.md").is_file()

    def test_publish_neutralizes_agent_triggers_before_posting(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: _agent_result("ping @@architect and @@dev now"),
        )

        outcome = engine.run()

        # The posted body must never carry a raw @@ token (issue #482), exactly
        # as ``prepare_comment._write_output`` guarantees on the legacy path.
        assert outcome.posted_body is not None
        assert "@@architect" not in outcome.posted_body
        assert "@@dev" not in outcome.posted_body
        assert "@@\u200barchitect" in outcome.posted_body
        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert "@@architect" not in journal
        assert "@@dev" not in journal

    def test_never_invokes_the_agent_when_the_guards_trip(self, tmp_path, monkeypatch):
        trigger = _trigger(body=f"<!-- bot-workflow-terminal -->\n{BOT_AGENT_MARKER}\n@@dev continue")
        engine = _engine(tmp_path, trigger)
        invoked = []
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: invoked.append(prompt))

        outcome = engine.run()

        assert invoked == []
        assert "agent" not in outcome.phases
        assert outcome.exit_code == ExitCode.TERMINAL

    def test_posts_the_terminal_skip_reason_when_the_workflow_is_missing(self, tmp_path, monkeypatch):
        """A failed ``/workflow`` resolution reaches the issue comment (issue #725).

        ``resolve_config`` reports the miss as ``SKIP_AGENT=true`` with the
        ``"/workflow '<name>': Workflow file not found ..."`` reason, but the
        ``guards`` phase currently returns ``posted_body=None``, so the reason
        dies in the logs. The terminal outcome must carry the reason through the
        adapter, reusing the legacy "Workflow stopped" skip comment.
        """
        engine = _engine(tmp_path)
        reason = (
            "/workflow 'spec-driven-impll': Workflow file not found: "
            "'spec-driven-impll.yaml' (searched: "
            "['/runner/workflows/spec-driven-impll.yaml'])"
        )
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "true", "REASON": reason})
        invoked = []
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: invoked.append(prompt))

        outcome = engine.run()

        assert invoked == []
        assert outcome.exit_code == ExitCode.TERMINAL
        assert outcome.posted_body is not None
        assert "spec-driven-impll" in outcome.posted_body
        assert "Workflow file not found" in outcome.posted_body
        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert "Workflow file not found" in journal

    def test_keeps_routine_guard_skips_silent(self, tmp_path, monkeypatch):
        """Only a resolver ``REASON`` is posted; routine guard skips stay silent.

        The legacy job exported ``REASON`` from ``resolve_config`` alone, so a
        same-agent loop / human-ping skip never posted a terminal comment. The
        fix must keep that distinction (issue #725).
        """
        trigger = _trigger(body=f"<!-- bot-workflow-terminal -->\n{BOT_AGENT_MARKER}\n@@dev continue")
        engine = _engine(tmp_path, trigger)
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "false", "TASK": "dev"})
        invoked = []
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: invoked.append(prompt))

        outcome = engine.run()

        assert invoked == []
        assert outcome.exit_code == ExitCode.TERMINAL
        assert outcome.posted_body is None

    def test_records_an_unrecoverable_failure_as_the_error_exit_code(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)

        def boom(self, prompt):
            raise RuntimeError("agent transport exploded")

        monkeypatch.setattr(Engine, "invoke_agent", boom)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR

    def test_posts_the_crash_fallback_when_the_agent_crashes(self, tmp_path, monkeypatch):
        """A crashed agent still reaches publish (issue #590 round-4, finding 2).

        The legacy job runs the agent behind ``| tee console.txt`` (so a non-zero
        exit never fails that step) and guards "Clean and Prepare Comment" with
        ``if: always()``. With no artefacts on disk the chain lands on its
        Path C/D fallback — the trigger-author ping plus the warning — and the
        driver must leave that same notice on the thread instead of returning
        silently.
        """
        engine = _engine(tmp_path)

        def boom(self, prompt):
            raise RuntimeError("agent transport exploded")

        monkeypatch.setattr(Engine, "invoke_agent", boom)

        outcome = engine.run()

        assert "publish" in outcome.phases
        assert outcome.posted_body is not None
        assert "@olivier-norture" in outcome.posted_body
        assert "did not produce a final message" in outcome.posted_body
        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert "did not produce a final message" in journal

    def test_posts_the_agents_own_answer_when_validation_fails(self, tmp_path, monkeypatch):
        """A rejected answer keeps the agent's text in the published body (finding 2)."""
        engine = _engine(tmp_path)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("*Dev answer*"))

        def reject(self, agent_result):
            raise ValueError("agent output failed validation: too short")

        monkeypatch.setattr(Engine, "validate", reject)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR
        assert outcome.posted_body is not None
        assert "*Dev answer*" in outcome.posted_body


class TestStatePhase:
    def _state(self):
        from workflow_state.workflow_state import WorkflowState

        return WorkflowState(
            run_id=3, workflow="spec-driven-impl", step="implement", agent="dev"
        )

    def test_persists_the_agents_own_state_artifact(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        state = self._state()
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: {**_agent_result("done"), "state": state}
        )

        outcome = engine.run()

        # The agent's artifact survives; the engine points the running step's
        # output locator at the comment it just posted (issue #756, ADR-0013).
        assert (outcome.state.workflow, outcome.state.step, outcome.state.agent) == (
            "spec-driven-impl",
            "implement",
            "dev",
        )
        locators = outcome.state.current_context["step_outputs"]
        assert [entry["step"] for entry in locators] == ["implement"]
        assert locators[0]["comment_id"]
        # The agent's own artifact object is never mutated in place.
        assert state.current_context == {}
        saved = json.loads((tmp_path / "runs" / "run-1" / "state.json").read_text())
        assert (saved["workflow"], saved["step"], saved["agent"]) == (
            "spec-driven-impl",
            "implement",
            "dev",
        )
        assert saved["current_context"]["step_outputs"][0]["step"] == "implement"

    def test_falls_back_to_the_previous_state(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        previous = self._state()
        engine.adapter.save_state(previous)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        outcome = engine.run()

        assert (outcome.state.workflow, outcome.state.step, outcome.state.agent) == (
            "spec-driven-impl",
            "implement",
            "dev",
        )
        assert [e["step"] for e in outcome.state.current_context["step_outputs"]] == [
            "implement"
        ]
        # The state loaded from the adapter is not mutated: the backfill is a copy.
        assert previous.current_context == {}

    def test_never_synthesizes_a_state_without_an_artifact_or_history(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        outcome = engine.run()

        assert outcome.state is None
        assert not (tmp_path / "runs" / "run-1" / "state.json").exists()


class TestPreviousStateHandoff:
    """The engine seeds the pre-agent state hand-off the refactor dropped.

    The legacy ``query_context`` step wrote the resolved state to
    ``$RUNNER_TEMP/prev_workflow_state.json`` before the agent ran. Without it
    the child starts from an empty state and resets ``history`` to a single
    entry (issues #726/#739). ``Engine.stage_prev_state`` restores the write from
    the adapter's own persistence; the codec-level regression against the real
    GitHub seam lives in ``test_github_adapter.TestStagePrevState``.
    """

    @staticmethod
    def _previous_state(*, history_len: int = 2):
        """A previous state carrying ``history_len`` history entries."""
        from config import StepRef
        from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
        from workflow_state.workflow_state import WorkflowState

        entries = [
            HistoryEntry(step=f"step_{index}", agent="architect", summary=f"s{index}")
            for index in range(history_len)
        ]
        return WorkflowState(
            run_id=3,
            workflow="spec-driven-impl",
            step="plan",
            agent="architect",
            position=[StepRef(workflow="spec-driven-impl", step="plan")],
            history=entries,
        )

    def test_run_stages_the_previous_state_before_invoking_the_agent(
        self, tmp_path, monkeypatch
    ):
        engine = _engine(tmp_path)
        engine.adapter.save_state(self._previous_state(history_len=2))
        seen: dict = {}

        def invoke(self, prompt):
            seen["state"] = self.adapter.load_state()
            return _agent_result("done")

        monkeypatch.setattr(Engine, "invoke_agent", invoke)

        engine.run()

        assert seen["state"] is not None
        assert len(seen["state"].history) == 2
        assert (seen["state"].workflow, seen["state"].step) == (
            "spec-driven-impl",
            "plan",
        )

    def test_writes_no_handoff_when_there_is_no_previous_state(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        seen: dict = {}

        def invoke(self, prompt):
            seen["state"] = self.adapter.load_state()
            return _agent_result("done")

        monkeypatch.setattr(Engine, "invoke_agent", invoke)

        engine.run()

        assert seen["state"] is None


class TestChildIdentityEnv:
    """The child's comment-based state recovery needs repo + issue (issue #739).

    ``load_prev_state_or_recover`` falls back to ``REPO_NAME``/``TARGET_NUMBER``
    when the ``$RUNNER_TEMP`` hand-off is missing; ``_ensure_child_identity``
    seeds both and fails loudly when neither source carries them, rather than
    writing an empty value that makes the recovery silently return nothing.
    """

    def test_fills_the_issue_number_from_the_trigger(self):
        env = _ensure_child_identity({"GITHUB_REPOSITORY": "o/r"}, _trigger())

        assert env["TARGET_NUMBER"] == "590"

    def test_fills_the_repo_from_the_trigger_payload(self):
        trigger = Trigger(
            author="olivier-norture",
            body="@@dev fix it",
            type="issue_comment",
            number=590,
            payload={"repository": {"full_name": "olivier-norture/git-flow-ai"}},
        )

        env = _ensure_child_identity({"TARGET_NUMBER": "1"}, trigger)

        assert env["REPO_NAME"] == "olivier-norture/git-flow-ai"

    def test_fills_the_repo_from_the_repository_environment(self):
        env = _ensure_child_identity(
            {
                "GITHUB_REPOSITORY": "olivier-norture/git-flow-ai",
                "TARGET_NUMBER": "590",
            },
            None,
        )

        assert env["REPO_NAME"] == "olivier-norture/git-flow-ai"

    def test_an_existing_value_wins(self):
        env = _ensure_child_identity(
            {"REPO_NAME": "other/repo", "TARGET_NUMBER": "1"}, _trigger()
        )

        assert (env["REPO_NAME"], env["TARGET_NUMBER"]) == ("other/repo", "1")

    def test_empty_values_are_filled_not_left(self):
        """An empty ``REPO_NAME``/``TARGET_NUMBER`` is absent, not a value.

        ``$GITHUB_ENV`` interpolation can write an empty string; ``setdefault``
        would keep it and the child's comment recovery would silently return
        nothing (issue #739).
        """
        trigger = Trigger(
            author="olivier-norture",
            body="@@dev fix it",
            type="issue_comment",
            number=590,
            payload={"repository": {"full_name": "olivier-norture/git-flow-ai"}},
        )

        env = _ensure_child_identity({"REPO_NAME": "", "TARGET_NUMBER": ""}, trigger)

        assert (env["REPO_NAME"], env["TARGET_NUMBER"]) == (
            "olivier-norture/git-flow-ai",
            "590",
        )

    def test_a_missing_repo_is_fatal(self):
        with pytest.raises(ValueError, match="no repository"):
            _ensure_child_identity({"TARGET_NUMBER": "590"}, _trigger())

    def test_a_missing_issue_number_is_fatal(self):
        with pytest.raises(ValueError, match="no issue number"):
            _ensure_child_identity({"GITHUB_REPOSITORY": "o/r"}, None)

    def test_run_hands_the_child_the_repo_and_issue(self, tmp_path, monkeypatch):
        engine = _engine(
            tmp_path, env={"GITHUB_REPOSITORY": "olivier-norture/git-flow-ai"}
        )
        _stub_resolved(monkeypatch, {"ROLE_NAME": "dev"})
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        engine.run()

        env = engine._agent_env()
        assert env["REPO_NAME"] == "olivier-norture/git-flow-ai"
        assert env["TARGET_NUMBER"] == "590"


class TestWorkflowIdentityBackfill:
    """A bare human mention loses the workflow position in the resolver.

    ``build_state`` then appends a history entry with an empty ``step`` and no
    ``position`` (issue #739). The engine restores it from the state the run
    already resolved so the entry stays addressable.
    """

    @staticmethod
    def _state():
        from config import StepRef
        from workflow_state.workflow_state import WorkflowState

        return WorkflowState(
            run_id=3,
            workflow="spec-driven-impl",
            step="plan",
            agent="architect",
            position=[StepRef(workflow="spec-driven-impl", step="plan")],
        )

    def test_fills_missing_identity_from_the_previous_state(self):
        env = _backfill_workflow_identity({}, self._state())

        assert env["WORKFLOW_NAME"] == "spec-driven-impl"
        assert env["WORKFLOW_STEP"] == "plan"
        assert json.loads(env["WORKFLOW_POSITION"])["refs"] == [
            {"workflow": "spec-driven-impl", "step": "plan"}
        ]

    def test_keeps_the_resolvers_own_identity(self):
        env = _backfill_workflow_identity(
            {"WORKFLOW_NAME": "dev-review-loop", "WORKFLOW_STEP": "implement"},
            self._state(),
        )

        assert env["WORKFLOW_NAME"] == "dev-review-loop"
        assert env["WORKFLOW_STEP"] == "implement"

    def test_is_a_no_op_without_a_previous_state(self):
        assert _backfill_workflow_identity({"A": "b"}, None) == {"A": "b"}


class TestAgentEnvHandoff:
    """The engine hands the legacy child the resolution the bash job exported.

    The bash agent step read ``ROLE_NAME`` (and friends) back from
    ``$GITHUB_ENV``; without it the child cannot find its agent in
    ``agent-config.yaml``, boots the SDK with no model, and 404s on the first
    request (issue #717).
    """

    def test_carries_the_resolvers_role_name_to_the_child(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, {"ROLE_NAME": "dev", "TASK": "dev", "PROMPT_LOCATION": "/repo/dev.md"})

        env = engine._agent_env()

        assert env["ROLE_NAME"] == "dev"
        # The resolver's pick is re-resolved through the layered prompt lookup
        # (issue #741), so the child receives the shipped file's absolute path.
        assert env["PROMPT_LOCATION"] == str(repo_root() / "defaults" / "prompts" / "dev.md")

    def test_a_dispatch_trigger_hands_the_workflow_identity_to_the_child(
        self, tmp_path, monkeypatch
    ):
        """Plan step 3: the resolver's workflow/step reach the child (issue #739).

        The child rebuilds the history entry's ``step`` from ``WORKFLOW_STEP``,
        so a dispatch-triggered run must carry both that and ``WORKFLOW_NAME``.
        """
        engine = _engine(tmp_path)
        _stub_resolved(
            monkeypatch,
            {
                "ROLE_NAME": "dev",
                "TASK": "dev",
                "WORKFLOW_NAME": "spec-driven-impl",
                "WORKFLOW_STEP": "implement",
            },
        )

        env = engine._agent_env()

        assert env["WORKFLOW_NAME"] == "spec-driven-impl"
        assert env["WORKFLOW_STEP"] == "implement"

    def test_overlay_drops_the_engine_only_trigger(self):
        overlay = resolved_env_overlay({"ROLE_NAME": "dev", "ENGINE_TRIGGER": _trigger()})

        assert overlay == {"ROLE_NAME": "dev"}

    def test_overlay_stringifies_non_string_values(self):
        """A non-string setting must survive the hand-off (issue #717 round 2)."""
        overlay = resolved_env_overlay(
            {"ROLE_NAME": "dev", "CONFIG_TEMPERATURE": 0.7, "ENGINE_TRIGGER": _trigger()}
        )

        assert overlay == {"ROLE_NAME": "dev", "CONFIG_TEMPERATURE": "0.7"}

    def test_a_config_error_is_fatal(self, tmp_path, monkeypatch):
        """A rejected config must fail loudly, not boot the child modelless."""
        engine = _engine(tmp_path)

        def reject(self, trigger):
            raise ValueError("No LLM model resolved")

        monkeypatch.setattr(Engine, "resolve", reject)

        with pytest.raises(ValueError, match="No LLM model resolved"):
            engine._agent_env()

    def test_run_reuses_its_resolution_for_the_handoff(self, tmp_path, monkeypatch):
        """The child gets the very mapping the run routed on (issue #717 round 2)."""
        engine = _engine(tmp_path)
        calls: list[int] = []

        def resolver(**_):
            calls.append(1)
            return {"ROLE_NAME": "dev", "TASK": "dev", "PROMPT_LOCATION": "/repo/dev.md"}

        _patch_resolver(monkeypatch, resolver)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        engine.run()

        assert calls == [1]
        assert engine._agent_env()["ROLE_NAME"] == "dev"
        assert engine._role_of({}) == "dev"
        assert calls == [1]

    def test_backfills_the_workflow_identity_dropped_by_a_bare_mention(self, tmp_path, monkeypatch):
        """The child rebuilds the position from the seeded state (issue #739)."""
        engine = _engine(tmp_path)
        engine.adapter.save_state(TestPreviousStateHandoff._previous_state(history_len=1))
        _stub_resolved(monkeypatch, {"ROLE_NAME": "architect", "TASK": "architect"})
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        engine.run()

        env = engine._agent_env()
        assert env["WORKFLOW_NAME"] == "spec-driven-impl"
        assert env["WORKFLOW_STEP"] == "plan"
        assert json.loads(env["WORKFLOW_POSITION"])["refs"][-1] == {
            "workflow": "spec-driven-impl",
            "step": "plan",
        }

    def test_anchors_a_relative_config_path_to_the_checkout(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, env={"AGENT_CONFIG_PATH": "agent-config.yaml"})
        _stub_resolved(monkeypatch, {"ROLE_NAME": "dev"})

        env = engine._agent_env()

        assert env["AGENT_CONFIG_PATH"] == str(
            repo_root() / "defaults" / "agents" / "agent-config.yaml"
        )

    def test_keeps_an_absolute_config_path(self, tmp_path, monkeypatch):
        absolute = str(tmp_path / "agent-config.yaml")
        engine = _engine(tmp_path, env={"AGENT_CONFIG_PATH": absolute})
        _stub_resolved(monkeypatch, {"ROLE_NAME": "dev"})

        assert engine._agent_env()["AGENT_CONFIG_PATH"] == absolute

    def test_ambient_env_survives_a_resolver_failure(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, env={"AGENT_CONFIG_PATH": "agent-config.yaml"})

        def boom(self, resolved):
            raise RuntimeError("resolver exploded")

        monkeypatch.setattr(Engine, "resolve", boom)

        env = engine._agent_env()

        assert env["AGENT_CONFIG_PATH"] == str(
            repo_root() / "defaults" / "agents" / "agent-config.yaml"
        )


class TestRunLogArtifacts:
    """A run stages its logs in the adapter's artifact dir (issue #743).

    The agent child runs in a throwaway temp directory, so the SDK file log and
    the captured console output must be pointed at a host-owned staging dir the
    workflow uploads as the ``nebuko-task-logs`` artifact.
    """

    def test_the_agent_child_is_pointed_at_the_artifact_dir(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, env={"LOG_TO_FILE": "true"})
        _stub_resolved(monkeypatch, {"ROLE_NAME": "dev"})

        env = engine._agent_env()

        staging = engine.adapter.artifact_dir().resolve()
        assert env["LOG_TO_FILE"] == "true"
        assert env["LOG_DIR"] == str(staging)

    def test_publish_stages_the_console_log(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        result = {**_agent_result("done"), "console_log": "[phase:normalize] ok\n"}
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: result)

        engine.run()

        staged = engine.adapter.artifact_dir() / "console.txt"
        assert staged.read_text(encoding="utf-8") == "[phase:normalize] ok\n"

    def test_a_staging_failure_never_aborts_the_run(self, tmp_path, monkeypatch, caplog):
        engine = _engine(tmp_path)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("*Dev answer*"))

        from nebuko.engine.artifacts import RunArtifactStager

        def boom(self, console_log):
            raise OSError("read-only artifact dir")

        monkeypatch.setattr(RunArtifactStager, "bundle", boom)

        with caplog.at_level("WARNING"):
            outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.posted_body is not None
        assert "could not stage run logs" in caplog.text


class TestHonestPublish:
    """A dropped comment write must never look like a green run (issue #805).

    The transport retries transient/rate-limit failures and then raises
    ``PublishError``; these tests pin the engine's reaction: the undelivered
    body is staged as an artifact, the journal records the loss, and the
    process exits non-zero — while state and routing still advance so the run
    stays resumable.
    """

    def _run_with_failed_publish(self, tmp_path, monkeypatch, *, answer="*Dev answer*"):
        from nebuko.adapters.github import PublishError
        from workflow_state.workflow_state import WorkflowState

        engine = _engine(tmp_path)
        state = WorkflowState(version=3, workflow="default", step="implement", agent="dev")
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: {**_agent_result(answer), "state": state},
        )

        def failing_post(self, body):
            raise PublishError("secondary rate limit", None)

        monkeypatch.setattr(LocalAdapter, "post", failing_post)
        return engine, engine.run()

    def test_publish_failure_exits_non_zero(self, tmp_path, monkeypatch):
        _, outcome = self._run_with_failed_publish(tmp_path, monkeypatch)

        assert outcome.exit_code == ExitCode.ERROR
        assert outcome.routing.status == "hold"

    def test_state_and_routing_still_advance(self, tmp_path, monkeypatch):
        _, outcome = self._run_with_failed_publish(tmp_path, monkeypatch)

        assert "state" in outcome.phases
        assert "route" in outcome.phases
        assert (tmp_path / "runs" / "run-1" / "state.json").is_file()

    def test_undelivered_comment_survives_as_an_artifact(self, tmp_path, monkeypatch):
        engine, _ = self._run_with_failed_publish(tmp_path, monkeypatch, answer="the lost answer")

        staged = engine.adapter.artifact_dir() / "comment-body.md"
        assert "the lost answer" in staged.read_text(encoding="utf-8")

    def test_state_journal_records_the_publish_failure(self, tmp_path, monkeypatch):
        _, outcome = self._run_with_failed_publish(tmp_path, monkeypatch)

        assert outcome.state is not None
        assert outcome.state.publish_failed is True
        saved = json.loads((tmp_path / "runs" / "run-1" / "state.json").read_text())
        assert saved["publish_failed"] is True

    def test_a_successful_publish_leaves_the_flag_unset(self, tmp_path, monkeypatch):
        from workflow_state.workflow_state import WorkflowState

        engine = _engine(tmp_path)
        state = WorkflowState(version=3, workflow="default", step="implement", agent="dev")
        monkeypatch.setattr(
            Engine,
            "invoke_agent",
            lambda self, prompt: {**_agent_result("posted"), "state": state},
        )

        outcome = engine.run()

        assert outcome.exit_code != ExitCode.ERROR
        assert outcome.state is not None
        assert outcome.state.publish_failed is False
        assert not (engine.adapter.artifact_dir() / "comment-body.md").exists()


class TestAgentStartAcknowledgment:
    """The engine posts the acknowledgment the cutover dropped (issue #720).

    The monolith's ``Post Acknowledgment Comment`` step was not ported, so a run
    went silent between the trigger and the final answer. The engine now owns
    that emission; these tests read the local adapter's journal, which records
    every posted body in order.
    """

    def _journal(self, tmp_path, name: str = "run-1") -> str:
        return (tmp_path / "runs" / name / "journal.md").read_text(encoding="utf-8")

    def _run(self, tmp_path, monkeypatch, *, trigger=None, env=None, answer: str = "*Dev answer*"):
        engine = _engine(tmp_path, trigger, env=RUN_ENV if env is None else env)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result(answer))
        return engine.run()

    def test_posts_the_acknowledgment_linking_to_the_run(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        assert ACK_LINE in self._journal(tmp_path)

    def test_tags_the_acknowledgment_with_the_caller_marker(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        assert BOT_AGENT_MARKER in self._journal(tmp_path)

    def test_posts_the_acknowledgment_before_the_agent_answer(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        journal = self._journal(tmp_path)
        assert journal.index(ACK_LINE) < journal.index("*Dev answer*")

    def test_posts_exactly_one_acknowledgment_per_run(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        assert self._journal(tmp_path).count("agent started") == 1

    def test_skips_the_acknowledgment_without_an_issue_number(self, tmp_path, monkeypatch):
        outcome = self._run(tmp_path, monkeypatch, trigger=_trigger(number=0))

        assert "agent started" not in self._journal(tmp_path)
        assert outcome.exit_code == ExitCode.ADVANCE

    def test_a_failing_acknowledgment_never_aborts_the_run(self, tmp_path, monkeypatch):
        real_post = LocalAdapter.post

        def flaky_post(self, body):
            if "agent started" in body:
                raise RuntimeError("github unreachable")
            return real_post(self, body)

        monkeypatch.setattr(LocalAdapter, "post", flaky_post)

        outcome = self._run(tmp_path, monkeypatch)

        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.posted_body is not None
        assert "*Dev answer*" in outcome.posted_body

    def test_notify_posts_a_hand_built_notice_through_the_adapter(self, tmp_path):
        engine = _engine(tmp_path, env=RUN_ENV)

        engine.notify_agent_started(
            AgentStartNotice(agent="dev", role="dev", run_url=RUN_URL, issue_number=720)
        )

        assert ACK_LINE in self._journal(tmp_path)

    def test_notify_is_a_silent_no_op_without_an_issue_number(self, tmp_path):
        engine = _engine(tmp_path, env=RUN_ENV)

        engine.notify_agent_started(AgentStartNotice(agent="dev", role="dev", run_url=RUN_URL))

        assert not (tmp_path / "runs" / "run-1" / "journal.md").exists()

    def test_builds_the_notice_from_the_resolved_role(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, _trigger(number=720), env=RUN_ENV)
        _stub_resolved(monkeypatch, {"ROLE_NAME": "architect"})

        notice = engine._agent_start_notice(_trigger(number=720))

        assert notice == AgentStartNotice(
            agent="architect", role="architect", run_url=RUN_URL, issue_number=720
        )

    def test_links_the_run_from_the_github_env(self, tmp_path):
        engine = _engine(tmp_path, env=RUN_ENV)
        trigger = Trigger(
            author="olivier-norture",
            body="@@dev go",
            type="issue_comment",
            number=720,
            payload={"repository": {"full_name": "someone/fork"}},
        )

        assert engine._run_url(trigger) == RUN_URL

    def test_falls_back_to_the_event_repository(self, tmp_path):
        # Explicitly clear the repository the fixture seeds so the event payload
        # is the only source (the fallback under test).
        engine = _engine(tmp_path, env={"GITHUB_RUN_ID": "7", "GITHUB_REPOSITORY": ""})
        trigger = Trigger(
            author="olivier-norture",
            body="@@dev go",
            type="issue_comment",
            number=720,
            payload={"repository": {"full_name": "someone/fork"}},
        )

        assert engine._run_url(trigger) == "https://github.com/someone/fork/actions/runs/7"


class TestWorkflowStateAnswerComment:
    """The engine embeds the workflow state in the agent-answer comment (issue #736).

    A first cutover restoration (issue #727) posted the state as a separate,
    pre-agent notice — the wrong position. The legacy ``Add bot marker and
    workflow state to comment`` step prepends the state header to the answer
    itself, and ``get_last_state`` aligns the i-th state comment with the i-th
    history entry, so a separate comment would shift that alignment. These tests
    read the local adapter's journal, which records every posted body in order.
    """

    _ENTRY_RE = re.compile(r"^<!-- journal-entry @ .+? -->$", re.MULTILINE)

    def _bodies(self, tmp_path, name: str = "run-1") -> list[str]:
        text = (tmp_path / "runs" / name / "journal.md").read_text(encoding="utf-8")
        return [part.lstrip("\n").rstrip("\n") for part in self._ENTRY_RE.split(text)[1:]]

    def _answer(self, tmp_path) -> str:
        return next(body for body in self._bodies(tmp_path) if "*Dev answer*" in body)

    @staticmethod
    def _agent_state(history=None):
        from workflow_state.workflow_state import WorkflowState

        return WorkflowState(
            run_id=6,
            workflow="spec-driven-impl",
            step="implement",
            agent="dev",
            history=history or [],
        )

    def _run(self, tmp_path, monkeypatch, *, state=None):
        engine = _engine(tmp_path, env=RUN_ENV)
        result = _agent_result("*Dev answer*")
        if state is not None:
            result["state"] = state
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: result)
        return engine.run()

    def test_puts_the_workflow_state_on_the_answer_comment(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, state=self._agent_state())

        answer = self._answer(tmp_path)

        assert WORKFLOW_STATE_MARKER in answer
        assert "*Dev answer*" in answer

    def test_posts_a_single_state_bearing_comment(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, state=self._agent_state())

        assert sum(WORKFLOW_STATE_MARKER in body for body in self._bodies(tmp_path)) == 1

    def test_puts_the_bot_marker_on_the_answer_comment_first_line(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, state=self._agent_state())

        assert self._answer(tmp_path).splitlines()[0] == "<!-- bot-workflow-terminal -->"

    def test_carries_the_continue_token_for_an_advancing_run(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, state=self._agent_state())

        assert "<!-- workflow-continue -->" in self._answer(tmp_path)

    def test_the_state_travels_after_the_acknowledgment(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, state=self._agent_state())

        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert journal.index("agent started") < journal.index(WORKFLOW_STATE_MARKER)

    def test_omits_the_state_section_without_an_agent_artifact(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        assert WORKFLOW_STATE_MARKER not in self._answer(tmp_path)

    def test_escapes_agent_triggers_in_the_answer_comment(self, tmp_path, monkeypatch):
        """A raw ``@@agent`` token in the state must not reach the posted body.

        Per issue #482 an unescaped ``@@agent`` token in a posted comment
        re-arms the very gate that started the run, and the state's ``history``
        summaries are LLM-authored prose. ``publish`` therefore runs the framed
        body through ``escape_agent_triggers`` before ``adapter.post``.
        """
        from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry

        state = self._agent_state(
            history=[HistoryEntry(step="implement", agent="dev", summary="ping @@dev then continue")]
        )
        self._run(tmp_path, monkeypatch, state=state)

        answer = self._answer(tmp_path)
        assert "@@dev" not in answer
        assert "@@\u200bdev" in answer


class TestCapturedDeliverableComment:
    """The posted comment is the engine-captured deliverable (issue #740).

    A terminal ``finish``/``dispatch_next`` turn carries routing metadata, not
    the deliverable, so ``publish`` frames the ``deliverable`` key. Routing and
    validation keep reading the legacy ``answer`` (the dispatch sentinel is
    parsed from it), so the capture can never reroute a run.
    """

    _ROUTING_NOTE = "*Dev answer* routing note"
    _DELIVERABLE = "*Dev answer* the full deliverable"

    def test_posts_the_captured_deliverable(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, env=RUN_ENV)
        result = {"answer": self._ROUTING_NOTE, "deliverable": self._DELIVERABLE}
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: result)

        outcome = engine.run()

        assert self._DELIVERABLE in outcome.posted_body
        assert self._ROUTING_NOTE not in outcome.posted_body

    def test_keeps_routing_on_the_legacy_answer(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, env=RUN_ENV)
        result = {
            "answer": self._ROUTING_NOTE
            + '\n<!-- AGENT_RUN_CONFIG {"task": "dev", "dispatch": {"status": "hold"}} -->',
            "deliverable": self._DELIVERABLE,
        }
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: result)

        outcome = engine.run()

        assert outcome.routing.status == "hold"
        assert outcome.exit_code == ExitCode.HOLD


class TestIssueBodyContext:
    """The issue body reaches the rendered workflow-run context (issue #768)."""

    def _rendered(self, tmp_path, trigger) -> str:
        engine = _engine(tmp_path, trigger=trigger)
        return engine._render_context({_TRIGGER_KEY: trigger}, [])

    def test_issue_comment_renders_the_issue_body_before_the_comment(self, tmp_path):
        trigger = _trigger(body="/nebuko run workflow", issue_body="the real task")

        rendered = self._rendered(tmp_path, trigger)

        assert "**Issue body:**\nthe real task" in rendered
        assert rendered.index("**Issue body:**") < rendered.index("/nebuko run workflow")

    def test_issue_trigger_does_not_duplicate_the_issue_body(self, tmp_path):
        trigger = Trigger(
            author="olivier-norture",
            body="make it so",
            type="issue",
            number=590,
            issue_body="make it so",
        )

        rendered = self._rendered(tmp_path, trigger)

        assert "**Issue body:**" not in rendered
        assert rendered.count("make it so") == 1

    def test_empty_issue_body_leaves_the_context_unchanged(self, tmp_path):
        rendered = self._rendered(tmp_path, _trigger())

        assert "**Issue body:**" not in rendered
        assert rendered == (
            "**Author:** olivier-norture\n\n"
            "@@dev fix it\n\n"
            "## Recent thread\n\n_(no prior thread)_"
        )


class TestHistoryWindowInjection:
    """Issue #844: bound the injected thread and drop engine bookkeeping.

    The recency window is the sole bound on prompt size (ADR-0003, no
    summarizer), so the injected thread must be capped and every comment body
    must reach the prompt without the collapsed workflow-state block or the bot
    routing markers.
    """

    def test_engine_defaults_to_the_bounded_window(self, tmp_path):
        engine = _engine(tmp_path)

        assert engine.history_limit == 5

    def test_main_wires_the_window_from_the_environment(
        self, tmp_path, monkeypatch, mocker
    ):
        import nebuko.engine.run_loop as run_loop

        monkeypatch.setenv("HISTORY_COMMENT_COUNT", "2")
        run = mocker.patch.object(
            run_loop.Engine,
            "run",
            autospec=True,
            return_value=mocker.Mock(exit_code=0),
        )

        run_loop.main(["--adapter", "local", "--run-dir", str(tmp_path)])

        engine = run.call_args[0][0]
        assert engine.history_limit == 2

    def test_run_loads_the_thread_with_the_bounded_window(self, tmp_path, monkeypatch, mocker):
        engine = _engine(tmp_path)
        spy = mocker.spy(engine.adapter, "load_history")
        _stub_resolved(monkeypatch, {"SKIP_AGENT": "true", "REASON": "no-op"})

        engine.run()

        assert spy.call_args.kwargs["limit"] == 5

    def test_render_history_strips_engine_bookkeeping(self, tmp_path):
        from nebuko.engine.models import CommentEntry

        engine = _engine(tmp_path)
        history = [
            CommentEntry(
                author="bot",
                body="human readable\n\n" + _soft_kill_comment_body(),
                created_at="2026-10-01T07:47:47Z",
            )
        ]

        rendered = engine._render_context({_TRIGGER_KEY: _trigger()}, history)

        assert "human readable" in rendered
        assert "### bot — 2026-10-01T07:47:47Z" in rendered
        assert WORKFLOW_STATE_MARKER not in rendered
        assert "WORKFLOW_STATE_END" not in rendered
        assert "Workflow State" not in rendered
        assert "<!-- bot-workflow-terminal -->" not in rendered
        assert "<!-- bot-agent-dev -->" not in rendered
        assert "<!-- workflow-continue -->" not in rendered

    def test_render_context_strips_a_state_bearing_trigger(self, tmp_path):
        trigger = _trigger(body="do the rebase\n\n" + _soft_kill_comment_body())
        engine = _engine(tmp_path, trigger)

        rendered = engine._render_context({_TRIGGER_KEY: trigger}, [])

        assert "do the rebase" in rendered
        assert "**Author:** olivier-norture" in rendered
        assert WORKFLOW_STATE_MARKER not in rendered
        assert "<!-- bot-workflow-terminal -->" not in rendered
        assert "<!-- bot-agent-dev -->" not in rendered


class TestBuildPromptStepReplacement:
    """A step's prompt can replace the role prompt (issue #784)."""

    def _resolved(self, tmp_path, base_text: str, step_text: str, replace: str) -> dict:
        base = tmp_path / "dev.md"
        base.write_text(base_text, encoding="utf-8")
        step = tmp_path / "step.md"
        step.write_text(step_text, encoding="utf-8")
        return {
            "PROMPT_LOCATION": str(base),
            "STEP_PROMPT_FILE": str(step),
            "STEP_PROMPT_REPLACE": replace,
        }

    def test_prompt_replace_drops_the_role_prompt(self, tmp_path):
        engine = _engine(tmp_path)
        resolved = self._resolved(tmp_path, "ROLE DEV FLOW", "SCAFFOLD ONLY", "true")

        prompt = engine.build_prompt(resolved, [])

        assert "ROLE DEV FLOW" not in prompt
        assert "SCAFFOLD ONLY" in prompt

    def test_default_keeps_the_role_prompt_additive(self, tmp_path):
        engine = _engine(tmp_path)
        resolved = self._resolved(tmp_path, "ROLE DEV FLOW", "EXTRA STEP", "")

        prompt = engine.build_prompt(resolved, [])

        assert "ROLE DEV FLOW" in prompt
        assert "EXTRA STEP" in prompt

    def test_replace_without_a_step_prompt_keeps_the_role_prompt(self, tmp_path):
        engine = _engine(tmp_path)
        base = tmp_path / "dev.md"
        base.write_text("ROLE DEV FLOW", encoding="utf-8")
        resolved = {
            "PROMPT_LOCATION": str(base),
            "STEP_PROMPT_FILE": "",
            "STEP_PROMPT_REPLACE": "true",
        }

        prompt = engine.build_prompt(resolved, [])

        assert "ROLE DEV FLOW" in prompt


class TestStepInputSeam:
    """The engine materializes declared inputs *before* the agent (issue #813).

    These pin the run-loop seam the resolver's ``STEP_INPUTS`` feeds: a
    three-level kind-preserving lazy forwarding injects one forced block, a
    failed ``required`` input is terminal without invoking the agent, and an
    over-deep call-frame chain is refused the same deterministic way.
    """

    @staticmethod
    def _frame(callee: str, call_step: str, bindings: dict) -> dict:
        return {"callee": callee, "call_step": call_step, "bindings": bindings}

    @staticmethod
    def _step_inputs(frames, consumer, specs=None) -> dict:
        return {
            "STEP_INPUTS": json.dumps(
                {"frames": frames, "consumer": consumer, "specs": specs or {}}
            )
        }

    def test_three_level_forwarding_injects_one_forced_block(self, tmp_path):
        engine = _engine(tmp_path)
        role = tmp_path / "dev.md"
        role.write_text("ROLE DEV FLOW", encoding="utf-8")
        frames = [
            self._frame(
                "spec-driven", "call_spec",
                {"run_test": {"exec": ["sh", "-c", "echo 1 passed"]}},
            ),
            self._frame("spec-driven-impl", "build", {"run_test": "input.run_test"}),
        ]
        resolved = {
            "PROMPT_LOCATION": str(role),
            **self._step_inputs(frames, {"run_test": "input.run_test"}),
        }

        results, failure = engine._materialize_inputs(resolved, ["action"])

        assert failure is None
        assert [result.body for result in results] == ["1 passed\n"]
        prompt = engine.build_prompt(resolved, [], results)
        assert "## Step inputs (deterministic)" in prompt
        assert "1 passed" in prompt

    def test_required_input_failure_is_terminal_before_the_agent(self, tmp_path):
        engine = _engine(tmp_path)
        resolved = self._step_inputs(
            [],
            {"diff": {"exec": ["sh", "-c", "exit 2"]}},
            {"diff": {"kind": "exec", "required": True}},
        )

        _, failure = engine._materialize_inputs(resolved, ["action"])

        assert failure is not None
        assert failure.exit_code == ExitCode.TERMINAL
        assert "required step input failed" in failure.routing.reason

    def test_over_deep_call_chain_is_terminal_before_the_agent(self, tmp_path):
        from nebuko.engine.input_wiring import MAX_CALL_FRAMES

        engine = _engine(tmp_path)
        frames = [
            self._frame(f"wf-{i}", f"s{i}", {})
            for i in range(MAX_CALL_FRAMES + 1)
        ]

        results, failure = engine._materialize_inputs(
            self._step_inputs(frames, {"x": {"str": "v"}}), ["action"]
        )

        assert results == []
        assert failure is not None
        assert failure.exit_code == ExitCode.TERMINAL
        assert "sub-workflow call depth exceeded" in failure.routing.reason

    def test_resolver_emission_feeds_the_engine_seam(self, tmp_path):
        """End-to-end: the trio's YAML is resolved, then materialized (issue #813).

        The resolver emits ``STEP_INPUTS`` from the carried-forward mount chain
        and the engine runs the forwarded ``exec`` exactly once at the
        consuming step — the same path a live run takes, without mocking either
        side.
        """
        import tempfile

        from config import StepRef, WorkflowPosition
        from resolve_config.features.build_result.build_result import _build_result
        from resolve_workflow import TASK_TO_PROMPT

        prompt_base = tempfile.mkdtemp()
        for name in set(TASK_TO_PROMPT.values()):
            Path(prompt_base, name).write_text("# prompt", encoding="utf-8")
        self._write_trio(tmp_path)

        base = WorkflowPosition(refs=[
            StepRef(workflow="super-parent", step="call_spec"),
            StepRef(workflow="spec-driven", step="build"),
        ])
        resolved = _build_result(
            {
                "task": "dev",
                "config_workflow": "spec-driven",
                "config_step": "build",
                "config_position": base,
            },
            prompt_base,
            str(tmp_path),
        )

        engine = _engine(tmp_path)
        results, failure = engine._materialize_inputs(resolved, ["action"])

        assert failure is None
        assert [result.body for result in results] == ["1 passed\n"]
        prompt = engine.build_prompt(resolved, [], results)
        assert "1 passed" in prompt

    @staticmethod
    def _write_trio(workflows_dir) -> None:
        from pathlib import Path as _Path

        (_Path(workflows_dir) / "super-parent.yaml").write_text(
            "name: super-parent\n"
            "description: root\n"
            "steps:\n"
            "  - name: call_spec\n"
            "    workflow: spec-driven\n"
            "    inputs:\n"
            "      run_test:\n"
            '        exec: ["echo", "1 passed"]\n',
            encoding="utf-8",
        )
        (_Path(workflows_dir) / "spec-driven.yaml").write_text(
            "name: spec-driven\n"
            "description: middle\n"
            "inputs:\n"
            "  - name: run_test\n"
            "    kind: exec\n"
            "steps:\n"
            "  - name: build\n"
            "    workflow: spec-driven-impl\n"
            "    inputs:\n"
            "      run_test: input.run_test\n",
            encoding="utf-8",
        )
        (_Path(workflows_dir) / "spec-driven-impl.yaml").write_text(
            "name: spec-driven-impl\n"
            "description: leaf\n"
            "inputs:\n"
            "  - name: run_test\n"
            "    kind: exec\n"
            "steps:\n"
            "  - name: stub\n"
            "    agent: dev\n"
            "    inputs:\n"
            "      run_test: input.run_test\n",
            encoding="utf-8",
        )


class TestEntryPoints:
    def test_main_takes_an_argv_sequence(self):
        assert list(inspect.signature(main).parameters) == ["argv"]

    def test_run_module_reexports_main(self):
        from nebuko.engine.run import main as run_main

        assert run_main is main

    def test_module_entry_point_reexports_main(self):
        import nebuko.engine.__main__ as entry_point

        assert entry_point.main is main

    def test_package_reexports_the_engine_surface(self):
        from nebuko import engine

        assert engine.main is main
        assert engine.Engine is Engine
        assert tuple(engine.PHASES) == PHASES


class TestLlmSessionIdStamp:
    """The engine stamps the trigger's issue id for session-aware gateways (#854).

    The value is the same validated issue number ``_ensure_child_identity``
    already seeds as ``TARGET_NUMBER``, so no second validation path exists.
    """

    def test_stamps_the_validated_trigger_number(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, trigger=_trigger(number=854))
        _stub_resolved(monkeypatch, {"ROLE_NAME": "dev"})

        assert engine._agent_env()["LLM_SESSION_ID"] == "854"

    def test_does_not_clobber_an_ambient_session_id(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, env={"LLM_SESSION_ID": "explicit"})
        _stub_resolved(monkeypatch, {"ROLE_NAME": "dev"})

        assert engine._agent_env()["LLM_SESSION_ID"] == "explicit"

    def test_the_pure_stamp_matches_the_child_number(self):
        assert _stamp_llm_session_id({"TARGET_NUMBER": "854"})["LLM_SESSION_ID"] == "854"

    def test_the_pure_stamp_is_a_noop_without_a_number(self):
        assert "LLM_SESSION_ID" not in _stamp_llm_session_id({})


class TestStepOutputScope:
    """Step-name data-flow at the engine seam (issue #756, ADR-0013).

    ``_step_output_scope`` builds the step-name -> state-stripped comment map
    that both the ``inputs`` materializer and prompt rendering consume.
    """

    @staticmethod
    def _comment(step, text, comment_id):
        from nebuko.engine.models import CommentEntry
        from workflow_state import WorkflowState, to_git_comment

        state = WorkflowState(workflow="w", step=step, agent="dev")
        body = f"{text}\n\n{to_git_comment(state)}"
        return CommentEntry(
            author="bot",
            body=body,
            created_at="2026-10-01T07:47:47Z",
            comment_id=comment_id,
        )

    def test_scope_maps_a_step_to_its_stripped_comment(self, tmp_path):
        engine = _engine(tmp_path)
        history = [self._comment("plan", "THE PLAN", "1")]
        scope = engine._step_output_scope(history)
        assert scope["plan"] == "THE PLAN"

    def test_newest_comment_wins_after_a_loop_rerun(self, tmp_path):
        engine = _engine(tmp_path)
        history = [
            self._comment("plan", "FIRST PLAN", "1"),
            self._comment("plan", "SECOND PLAN", "2"),
        ]
        assert engine._step_output_scope(history)["plan"] == "SECOND PLAN"

    def test_empty_history_yields_an_empty_scope(self, tmp_path):
        assert _engine(tmp_path)._step_output_scope([]) == {}

    def test_render_step_prompt_substitutes_a_known_step(self, tmp_path):
        engine = _engine(tmp_path)
        rendered = engine._render_step_prompt("plan: {{ plan }}", {"plan": "P"})
        assert rendered == "plan: P"

    def test_render_step_prompt_degrades_an_unknown_step(self, tmp_path):
        engine = _engine(tmp_path)
        rendered = engine._render_step_prompt("{{ ghost }}", {"plan": "P"})
        assert rendered == "_(unavailable: ghost)_"
