"""Spec-scenario tests for the typed driver (issue #590, plan section 3.5).

The driver is the typed form of the 15-step ``run-task`` bash job: nine phases,
one ``[phase:<name>]`` log marker each, and an exit code that tells the host
whether to advance, stop for a human, or terminate.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

from adapters.local import LocalAdapter
from config import AgentStartNotice
from engine.models import ExitCode, RoutingDecision, RunOutcome, Trigger
from engine.paths import repo_root
from engine.run_loop import PHASES, Engine, main, resolved_env_overlay

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
ACK_LINE = f"🤖 **dev agent started** — [View workflow run logs]({RUN_URL})"


def _trigger(body: str = "@@dev fix it", number: int = 590) -> Trigger:
    return Trigger(author="olivier-norture", body=body, type="issue_comment", number=number)


def _engine(tmp_path, trigger=None, *, name: str = "run-1", env=None) -> Engine:
    adapter = LocalAdapter(tmp_path / "runs" / name, trigger=trigger or _trigger())
    return Engine(adapter=adapter, repo_root=repo_root(), env=dict(env or {"CIRCUIT_BREAKER_THRESHOLD": "3"}))


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


class TestPhaseVocabulary:
    def test_phases_are_the_nine_documented_phases_in_order(self):
        assert PHASES == (
            "normalize",
            "resolve_config",
            "guards",
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
        from engine.models import CommentEntry

        engine = _engine(tmp_path)
        history = [
            CommentEntry(author="bot", body=f"{BOT_AGENT_MARKER}\ncall {index}", created_at="2026-10-01T07:47:47Z")
            for index in range(6)
        ]

        verdict = engine.guard(_trigger(), history)

        assert str(verdict["CIRCUIT_BREAKER_TRIPPED"]).lower() == "true"


class TestRoutePhase:
    def test_maps_the_dispatch_payload(self, tmp_path):
        engine = _engine(tmp_path)

        decision = engine.route({"dispatch": {"status": "advance", "agent": "ctx", "complexity": "high"}})

        assert isinstance(decision, RoutingDecision)
        assert (decision.status, decision.agent, decision.complexity) == ("advance", "ctx", "high")

    def test_maps_a_human_gate_hold(self, tmp_path):
        engine = _engine(tmp_path)

        decision = engine.route({"dispatch": {"status": "hold", "reason": "await approval"}})

        assert decision.status == "hold"


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
            lambda self, prompt: _agent_result("ping @@genius and @@dev now"),
        )

        outcome = engine.run()

        # The posted body must never carry a raw @@ token (issue #482), exactly
        # as ``prepare_comment._write_output`` guarantees on the legacy path.
        assert outcome.posted_body is not None
        assert "@@genius" not in outcome.posted_body
        assert "@@dev" not in outcome.posted_body
        assert "@@\u200bgenius" in outcome.posted_body
        journal = (tmp_path / "runs" / "run-1" / "journal.md").read_text(encoding="utf-8")
        assert "@@genius" not in journal
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

    def test_records_an_unrecoverable_failure_as_the_error_exit_code(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)

        def boom(self, prompt):
            raise RuntimeError("agent transport exploded")

        monkeypatch.setattr(Engine, "invoke_agent", boom)

        outcome = engine.run()

        assert outcome.exit_code == ExitCode.ERROR

    def test_posts_the_crash_fallback_when_the_agent_crashes(self, tmp_path, monkeypatch):
        """A crashed agent still reaches publish (issue #590 round-4, finding 2).

        The legacy job runs the agent behind ``| tee console.log`` (so a non-zero
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
        assert outcome.posted_body.startswith("@olivier-norture")
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
            version=3, workflow="spec-driven-impl", step="implement", agent="dev"
        )

    def test_persists_the_agents_own_state_artifact(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        state = self._state()
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: {**_agent_result("done"), "state": state}
        )

        outcome = engine.run()

        assert outcome.state == state
        saved = json.loads((tmp_path / "runs" / "run-1" / "state.json").read_text())
        assert (saved["workflow"], saved["step"], saved["agent"]) == (
            "spec-driven-impl",
            "implement",
            "dev",
        )

    def test_falls_back_to_the_previous_state(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        previous = self._state()
        engine.adapter.save_state(previous)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        outcome = engine.run()

        assert outcome.state == previous

    def test_never_synthesizes_a_state_without_an_artifact_or_history(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("done"))

        outcome = engine.run()

        assert outcome.state is None
        assert not (tmp_path / "runs" / "run-1" / "state.json").exists()


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
        assert env["PROMPT_LOCATION"] == "/repo/dev.md"

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

    def test_anchors_a_relative_config_path_to_the_checkout(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, env={"AGENT_CONFIG_PATH": "agent-config.yaml"})
        _stub_resolved(monkeypatch, {"ROLE_NAME": "dev"})

        env = engine._agent_env()

        assert env["AGENT_CONFIG_PATH"] == str(repo_root() / "agent-config.yaml")

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

        assert env["AGENT_CONFIG_PATH"] == str(repo_root() / "agent-config.yaml")


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
        _stub_resolved(monkeypatch, {"ROLE_NAME": "genius"})

        notice = engine._agent_start_notice(_trigger(number=720))

        assert notice == AgentStartNotice(
            agent="genius", role="genius", run_url=RUN_URL, issue_number=720
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
        engine = _engine(tmp_path, env={"GITHUB_RUN_ID": "7"})
        trigger = Trigger(
            author="olivier-norture",
            body="@@dev go",
            type="issue_comment",
            number=720,
            payload={"repository": {"full_name": "someone/fork"}},
        )

        assert engine._run_url(trigger) == "https://github.com/someone/fork/actions/runs/7"


class TestEarlyWorkflowStateComment:
    """The engine emits the workflow state before the agent runs (issue #727).

    The cutover left ``WORKFLOW_STATE_START/END`` out of every comment because
    ``publish`` posts only the agent answer. The state must travel in its own
    comment, posted before the agent runs — exactly the legacy GHA step the
    cutover dropped. These tests read the local adapter's journal, which records
    every posted body in order.
    """

    _ENTRY_RE = re.compile(r"^<!-- journal-entry @ .+? -->$", re.MULTILINE)

    def _bodies(self, tmp_path, name: str = "run-1") -> list[str]:
        text = (tmp_path / "runs" / name / "journal.md").read_text(encoding="utf-8")
        return [part.lstrip("\n").rstrip("\n") for part in self._ENTRY_RE.split(text)[1:]]

    def _state_comment(self, tmp_path) -> str:
        return next(body for body in self._bodies(tmp_path) if WORKFLOW_STATE_MARKER in body)

    def _run(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, env=RUN_ENV)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("*Dev answer*"))
        return engine.run()

    def test_posts_the_state_before_the_agent_answer(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        bodies = self._bodies(tmp_path)
        state_index = next(i for i, body in enumerate(bodies) if WORKFLOW_STATE_MARKER in body)
        answer_index = next(i for i, body in enumerate(bodies) if "*Dev answer*" in body)

        assert state_index < answer_index

    def test_puts_the_bot_marker_on_the_state_comment_first_line(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        assert self._state_comment(tmp_path).splitlines()[0] == "<!-- bot-workflow-terminal -->"

    def test_the_answer_comment_carries_no_state(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        answer = next(body for body in self._bodies(tmp_path) if "*Dev answer*" in body)

        assert WORKFLOW_STATE_MARKER not in answer

    def test_carries_the_continue_token_for_an_advancing_run(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch)

        assert "<!-- workflow-continue -->" in self._state_comment(tmp_path)

    def test_escapes_agent_triggers_in_the_state_comment(self, tmp_path, monkeypatch):
        """A raw ``@@agent`` token in the state must not reach the posted body.

        Per issue #482 an unescaped ``@@agent`` token in a posted comment
        re-arms the very gate that started the run, and the state's ``history``
        summaries are LLM-authored prose. ``notify`` therefore runs the composed
        body through ``escape_agent_triggers`` before ``adapter.post``.
        """
        from workflow_state.sub_domains.history_entry.history_entry import HistoryEntry
        from workflow_state.workflow_state import WorkflowState

        previous = WorkflowState(
            version=6,
            workflow="spec-driven-impl",
            step="implement",
            agent="dev",
            history=[
                HistoryEntry(step="implement", agent="dev", summary="ping @@dev then continue")
            ],
        )
        engine = _engine(tmp_path, env=RUN_ENV)
        engine.adapter.save_state(previous)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("*Dev answer*"))
        engine.run()

        state_comment = self._state_comment(tmp_path)
        assert "@@dev" not in state_comment
        assert "@@\u200bdev" in state_comment


class TestEntryPoints:
    def test_main_takes_an_argv_sequence(self):
        assert list(inspect.signature(main).parameters) == ["argv"]

    def test_run_module_reexports_main(self):
        from engine.run import main as run_main

        assert run_main is main

    def test_module_entry_point_reexports_main(self):
        import engine.__main__ as entry_point

        assert entry_point.main is main

    def test_package_reexports_the_engine_surface(self):
        import engine

        assert engine.main is main
        assert engine.Engine is Engine
        assert tuple(engine.PHASES) == PHASES
