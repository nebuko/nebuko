"""Dispatched gate commands on the personal repo (issue #1482).

A ``repository_dispatch`` gate command has no comment author. The engine
verifies the dispatch actor (owner of the personal repo, write on the project
repo) and decides the gate as that actor. Each refusal yields a ``gate_noop``
notice that names a coarse reason and no token.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from nebuko.engine.lib.config import StepRef
from nebuko.engine.lib.resolve_config import resolve_config
from nebuko.engine.lib.resolve_config.sub_domains.dispatch_actor import dispatch_actor as da
from nebuko.engine.lib.workflow_state import (
    Appreciation,
    GateDecision,
    GateLock,
    TurnOutcome,
    TurnStatus,
    WorkflowState,
)

GATED_WORKFLOW_YAML = (
    "name: default\n"
    "description: Standard workflow with a gated approval step\n"
    "steps:\n"
    "  - name: plan\n"
    "    agent: architect\n"
    "    next: stub_approval\n"
    "  - name: stub_approval\n"
    "    agent: architect\n"
    "    human_gate: true\n"
    "    next: implement\n"
    "    conditions:\n"
    "      - on_result: approved\n"
    "        next: implement\n"
    "      - on_result: rejected\n"
    "        next: plan\n"
    "  - name: implement\n"
    "    agent: dev\n"
    "    next: review\n"
    "  - name: review\n"
    "    agent: review\n"
    "    next: null\n"
)

PERSONAL = "alice/nebuko-personal"
PROJECT = "acme/app"
TOKEN = "ghp_DO_NOT_LEAK_ME"


def _held_state() -> WorkflowState:
    step = StepRef(workflow="default", step="stub_approval")
    return WorkflowState(
        workflow="default",
        step="stub_approval",
        agent="architect",
        turn_outcome=TurnOutcome(status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED),
        position=[step],
        gate=GateLock(step="stub_approval"),
    )


def _decided_state(decision: GateDecision) -> WorkflowState:
    """The state after an approval: the gate is released and the workflow moved on."""
    return WorkflowState(
        workflow="default",
        step="implement",
        agent="dev",
        turn_outcome=TurnOutcome(status=TurnStatus.COMPLETED, appreciation=Appreciation.APPROVED),
        position=[StepRef(workflow="default", step="implement")],
        gate_history=[decision],
    )


def _body(command: str, state: WorkflowState) -> str:
    return "\n".join(
        [
            command,
            "<details>",
            "<summary>Workflow State</summary>",
            "",
            "```json",
            "<!-- WORKFLOW_STATE_START -->",
            state.model_dump_json(),
            "<!-- WORKFLOW_STATE_END -->",
            "```",
            "",
            "</details>",
        ]
    )


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Resolve a gate command dispatched to the personal repo by ``actor``."""
    for key in ("TRIGGER_AUTHOR_LOGIN", "TRIGGER_AUTHOR_TYPE", "TRIGGER_AUTHOR_ASSOCIATION",
                "REPO_NAME", "GH_TOKEN", "GITHUB_EVENT_PATH", "GITHUB_REPOSITORY",
                "GITHUB_REPOSITORY_OWNER", "GITHUB_EVENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    ws = tmp_path / "ws"
    (ws / ".nebuko").mkdir(parents=True)
    (ws / ".nebuko" / "nebuko.yaml").write_text("agent_config: agent-config-openrouter.yaml\n")
    personal = ws / ".nebuko-personal" / ".nebuko"
    personal.mkdir(parents=True)
    (personal / "nebuko.yaml").write_text("agent_config: agent-config-openrouter.yaml\n")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(ws))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "default.yaml").write_text(GATED_WORKFLOW_YAML)
    prompts = tmp_path / "prompts"
    for role in ("dev", "architect", "review"):
        (prompts / role).mkdir(parents=True)
        (prompts / role / "system.md").write_text(f"# {role} prompt")
    calls: list = []

    def _run(command, *, actor="alice", owner="alice", repository=PERSONAL,
             event="repository_dispatch", state=None, token=TOKEN, permission="write",
             gh_error=None, project_repo=PROJECT, lookup=True):
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps({"client_payload": {"actor": actor}}))
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
        monkeypatch.setenv("GITHUB_REPOSITORY", repository)
        monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", owner)
        monkeypatch.setenv("REPO_NAME", project_repo)
        monkeypatch.setenv("GH_TOKEN", token)
        monkeypatch.setenv("CALLER_AUTH", "COLLABORATOR")

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            if gh_error is not None:
                raise gh_error
            if "HTTP" in permission:
                return SimpleNamespace(stdout="", stderr=permission, returncode=1)
            return SimpleNamespace(stdout=permission + "\n", stderr="", returncode=0)

        monkeypatch.setattr(da.subprocess, "run", fake_run)
        body = _body(command, state or _held_state())
        result = resolve_config(
            trigger_body=body,
            prompt_base=str(prompts),
            workflows_dir=str(workflows),
            is_bot=False,
        )
        return result

    _run.calls = calls
    return _run


def _decision_of(result) -> GateDecision:
    assert "GATE_DECISION" in result, {k: result.get(k) for k in ("SKIP_AGENT", "REASON", "TASK")}
    return GateDecision.model_validate_json(result["GATE_DECISION"])


class TestOwnerDispatchDecidesGate:
    def test_owner_with_write_approves_and_records_the_owner(self, run):
        result = run("/nebuko approve gate")
        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "dev"
        decision = _decision_of(result)
        assert decision.decided_by == "alice"
        assert decision.status == "approved"
        assert result.get("GATE_RELEASED") == "true"

    def test_owner_with_write_rejects_the_same_way(self, run):
        result = run("/nebuko reject gate")
        assert result["SKIP_AGENT"] == "false"
        decision = _decision_of(result)
        assert decision.decided_by == "alice"
        assert decision.status == "rejected"
        assert result.get("GATE_RELEASED") == "true"

    def test_lookup_runs_on_the_project_repo_with_the_payload_token(self, run):
        run("/nebuko approve gate")
        argv, kwargs = run.calls[0]
        assert f"repos/{PROJECT}/collaborators/alice/permission" in argv[2]
        assert kwargs["env"]["GH_TOKEN"] == TOKEN
        assert TOKEN not in " ".join(argv)


class TestRefusedDispatchGate:
    def test_non_owner_dispatch_is_refused_without_a_lookup(self, run):
        result = run("/nebuko approve gate", actor="bob")
        assert result["SKIP_AGENT"] == "true"
        assert "GATE_DECISION" not in result
        assert run.calls == []

    def test_owner_without_write_is_refused_with_the_no_write_reason(self, run):
        result = run("/nebuko approve gate", permission="read")
        assert result["SKIP_AGENT"] == "true"
        assert result["SKIP_KIND"] == "gate_noop"
        assert "no write access" in result["REASON"]
        assert "GATE_DECISION" not in result

    def test_owner_not_a_collaborator_reads_as_no_write(self, run):
        result = run("/nebuko approve gate", permission="none")
        assert "no write access" in result["REASON"]

    def test_lookup_403_is_named_as_a_github_refusal_not_no_write(self, run):
        result = run("/nebuko approve gate", permission="HTTP 403: Must have admin rights")
        assert result["SKIP_KIND"] == "gate_noop"
        assert "refused the permission lookup" in result["REASON"]
        assert "no write access" not in result["REASON"]

    def test_lookup_404_is_named_as_a_github_refusal_not_no_write(self, run):
        result = run("/nebuko approve gate", permission="HTTP 404: Not Found")
        assert "refused the permission lookup" in result["REASON"]
        assert "no write access" not in result["REASON"]

    def test_lookup_timeout_is_refused_and_named(self, run):
        result = run(
            "/nebuko approve gate",
            gh_error=subprocess.TimeoutExpired(cmd="gh", timeout=30),
        )
        assert result["SKIP_KIND"] == "gate_noop"
        assert "timed out" in result["REASON"]
        assert "GATE_DECISION" not in result

    def test_empty_lookup_token_is_refused_without_calling_gh(self, run):
        result = run("/nebuko approve gate", token="   ")
        assert "no project-repo token" in result["REASON"]
        assert run.calls == []

    def test_refusal_notice_carries_no_token(self, run):
        for kwargs in ({"permission": "HTTP 403"}, {"permission": "read"}, {"token": "  "}):
            result = run("/nebuko approve gate", **kwargs)
            assert TOKEN not in result.get("REASON", "")

    def test_project_repo_dispatch_keeps_the_unchanged_refusal(self, run):
        result = run("/nebuko approve gate", repository=PROJECT, owner="acme")
        assert result["SKIP_AGENT"] == "true"
        assert "GATE_DECISION" not in result
        assert "not authorized to decide a gate" in result["REASON"]
        assert run.calls == []

    def test_reject_by_a_non_owner_is_refused(self, run):
        result = run("/nebuko reject gate", actor="bob")
        assert "GATE_DECISION" not in result
        assert run.calls == []


class TestDispatchedGateLockHolds:
    def test_second_approve_after_the_decision_is_a_clean_noop(self, run):
        first = run("/nebuko approve gate")
        decision = _decision_of(first)
        result = run("/nebuko approve gate", state=_decided_state(decision))
        assert result["SKIP_AGENT"] == "true"
        assert result["SKIP_KIND"] == "gate_noop"
        assert "GATE_DECISION" not in result

    def test_redelivered_approve_on_the_same_held_state_is_single_shot(self, run):
        first = run("/nebuko approve gate")
        decision = _decision_of(first)
        held_with_decision = _held_state()
        held_with_decision.gate_history = [decision]
        result = run("/nebuko approve gate", state=held_with_decision)
        assert result["SKIP_KIND"] == "gate_noop"
        assert "single-shot" in result["REASON"]
        assert "GATE_DECISION" not in result
