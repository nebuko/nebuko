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
from engine.run_loop import (
    PHASES,
    Engine,
    _backfill_workflow_identity,
    _ensure_child_identity,
    main,
    resolved_env_overlay,
)

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
            HistoryEntry(step=f"step_{index}", agent="genius", summary=f"s{index}")
            for index in range(history_len)
        ]
        return WorkflowState(
            version=3,
            workflow="spec-driven-impl",
            step="plan",
            agent="genius",
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
            version=3,
            workflow="spec-driven-impl",
            step="plan",
            agent="genius",
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
        assert env["PROMPT_LOCATION"] == "/repo/dev.md"

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
        _stub_resolved(monkeypatch, {"ROLE_NAME": "genius", "TASK": "genius"})
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

        staged = engine.adapter.artifact_dir() / "console.log"
        assert staged.read_text(encoding="utf-8") == "[phase:normalize] ok\n"

    def test_a_staging_failure_never_aborts_the_run(self, tmp_path, monkeypatch, caplog):
        engine = _engine(tmp_path)
        monkeypatch.setattr(Engine, "invoke_agent", lambda self, prompt: _agent_result("*Dev answer*"))

        from engine.artifacts import RunArtifactStager

        def boom(self, console_log):
            raise OSError("read-only artifact dir")

        monkeypatch.setattr(RunArtifactStager, "bundle", boom)

        with caplog.at_level("WARNING"):
            outcome = engine.run()

        assert outcome.exit_code == ExitCode.ADVANCE
        assert outcome.posted_body is not None
        assert "could not stage run logs" in caplog.text


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
            version=6,
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
