"""Lane takeover, move-back and switch through resolve_config and the credential guard (#1479).

The personal repo's run is a repository_dispatch: its actor comes from
``client_payload.actor`` and counts only when it equals ``GITHUB_REPOSITORY_OWNER``.
The engine's decision reaches the state writer and the credential guard through
the resolved ``WORKFLOW_LANE`` / ``CLAUDE_CREDENTIAL_OWNER`` / ``NEBUKO_LANE_ACTION``
keys, which the caller's environment carries after the resolve step.
"""

from __future__ import annotations

import importlib
import json
import os
from types import SimpleNamespace

import pytest

from nebuko.engine.harness.credentials import (
    USER_REPO_LANE,
    ContinuationContext,
    authorize_lane_binding,
)
from nebuko.engine.lib.default_paths import checkout_root
from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState, lane_binding_from_env

rc = importlib.import_module("nebuko.engine.lib.resolve_config.resolve_config")
run_agent = importlib.import_module("nebuko.engine.lib.agent_script.features.run_agent.run_agent")
codec_mod = importlib.import_module("nebuko.engine.lib.workflow_state.state_codec")

LANE_KEYS = ("WORKFLOW_LANE", "CLAUDE_CREDENTIAL_OWNER", "NEBUKO_LANE_ACTION")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".nebuko").mkdir(parents=True)
    (ws / ".nebuko" / "nebuko.yaml").write_text("agent_config: agent-config-mistral.yaml\n")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(ws))
    for key in LANE_KEYS + (
        "AGENT_CONFIG_PATH",
        "TRIGGER_AUTHOR_LOGIN",
        "TRIGGER_AUTHOR_TYPE",
        "GITHUB_REPOSITORY",
        "GITHUB_REPOSITORY_OWNER",
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return ws


def _personal_run(monkeypatch, workspace, *, owner, actor, prev=None):
    """A personal-repo dispatch run: ``owner/nebuko-personal`` dispatched by ``actor``."""
    event = workspace.parent / "event.json"
    event.write_text(json.dumps({"client_payload": {"actor": actor}}))
    monkeypatch.setenv("GITHUB_REPOSITORY", f"{owner}/nebuko-personal")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", owner)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "repository_dispatch")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    personal = workspace / ".nebuko-personal" / ".nebuko"
    personal.mkdir(parents=True)
    (personal / "nebuko.yaml").write_text("agent_config: agent-config-openrouter.yaml\n")
    monkeypatch.setattr(rc, "_fetch_workflow_state_from_history", lambda repo, number: prev)


def _project_comment(monkeypatch, workspace, *, author, prev=None):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    monkeypatch.setenv("TRIGGER_AUTHOR_LOGIN", author)
    monkeypatch.setenv("TRIGGER_AUTHOR_TYPE", "User")
    monkeypatch.setattr(rc, "_fetch_workflow_state_from_history", lambda repo, number: prev)


def _resolve(body):
    return rc.resolve_config(
        trigger_body=body,
        prompt_base="",
        workflows_dir=str(checkout_root() / "defaults" / "workflows"),
        is_bot=False,
    )


def _user_repo_state(owner="alice"):
    return WorkflowState(
        workflow="default", step="implement", lane=USER_REPO_LANE, credential_owner=owner,
        agent_config="agent-config-openrouter.yaml", started_by=owner,
    )


def _export(result):
    """Carry the resolve step's lane keys into the environment, as the runner does."""
    return {key: result[key] for key in LANE_KEYS if key in result}


class TestPersonalRunTakeover:
    def test_takeover_records_the_new_owner_and_notice(self, workspace, monkeypatch):
        _personal_run(monkeypatch, workspace, owner="bob", actor="bob", prev=_user_repo_state("alice"))
        result = _resolve("/nebuko run workflow --name default --personal")

        assert result.get("COMMAND_ERROR") != "true"
        assert result["LANE_NOTICE"] == "credential owner changed from alice to bob"
        assert result["WORKFLOW_LANE"] == USER_REPO_LANE
        assert result["CLAUDE_CREDENTIAL_OWNER"] == "bob"
        assert result["NEBUKO_LANE_ACTION"] == "takeover"

    def test_takeover_passes_the_binding_guard_and_writes_the_new_owner(self, workspace, monkeypatch):
        prev = _user_repo_state("alice")
        _personal_run(monkeypatch, workspace, owner="bob", actor="bob", prev=prev)
        result = _resolve("/nebuko run workflow --name default --personal")
        for key, value in _export(result).items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("TRIGGER_AUTHOR_LOGIN", "bob")
        monkeypatch.setenv("TRIGGER_AUTHOR_TYPE", "User")
        monkeypatch.setattr(
            codec_mod.WorkflowStateCodec,
            "load_prev",
            lambda self: SimpleNamespace(state=prev, refused_newer_lane=False),
        )
        monkeypatch.setattr(codec_mod.WorkflowStateCodec, "untrusted_handoff_marked", lambda self: False)

        ctx = run_agent._trigger_facts_from_env()
        assert authorize_lane_binding(ctx).allowed
        # The state writer reads the same keys: the new owner is recorded.
        assert lane_binding_from_env(os.environ, "bob") == (USER_REPO_LANE, "bob")

    def test_without_the_decision_the_binding_guard_refuses_a_takeover(self, workspace, monkeypatch):
        prev = _user_repo_state("alice")
        decision = authorize_lane_binding(
            ContinuationContext(
                trigger_login="bob",
                trigger_type="User",
                trusted_bots=(),
                comment_header="",
                started_by="alice",
                credential_owner="bob",
                lane=USER_REPO_LANE,
                previous_lane=USER_REPO_LANE,
                previous_credential_owner=prev.credential_owner,
            )
        )
        assert not decision.allowed and decision.failed_rule == "lane-binding"

    def test_dispatch_from_a_non_owner_is_refused(self, workspace, monkeypatch):
        _personal_run(monkeypatch, workspace, owner="alice", actor="bob", prev=_user_repo_state("alice"))
        result = _resolve("/nebuko run workflow --name default --personal")

        assert result["SKIP_AGENT"] == "true"
        assert result["COMMAND_ERROR"] == "true"
        assert "not the owner" in result["REASON"]
        assert "WORKFLOW_LANE" not in result

    def test_dispatch_without_an_actor_is_refused(self, workspace, monkeypatch):
        _personal_run(monkeypatch, workspace, owner="bob", actor="bob")
        event = workspace.parent / "event.json"
        event.write_text(json.dumps({"client_payload": {}}))
        result = _resolve("/nebuko run workflow --name default --personal")

        assert result["COMMAND_ERROR"] == "true"
        assert "unknown actor" in result["REASON"]


class TestPersonalSwitchAndMoveBack:
    def test_project_run_switches_to_the_personal_credential(self, workspace, monkeypatch):
        prev = WorkflowState(workflow="default", step="implement", lane=None, agent_config=None)
        _personal_run(monkeypatch, workspace, owner="alice", actor="alice", prev=prev)
        result = _resolve("/nebuko run workflow --name default --personal")

        assert result["LANE_NOTICE"] == "lane switched to the personal credential of alice"
        assert result["WORKFLOW_LANE"] == USER_REPO_LANE
        assert result["CLAUDE_CREDENTIAL_OWNER"] == "alice"

    def test_project_move_back_records_the_shared_lane(self, workspace, monkeypatch):
        _project_comment(monkeypatch, workspace, author="alice", prev=_user_repo_state("alice"))
        result = _resolve("/nebuko run workflow --name default --project")

        assert result["LANE_NOTICE"] == "lane moved back to the project"
        assert result["WORKFLOW_LANE"] == "shared"
        assert result["CLAUDE_CREDENTIAL_OWNER"] == ""
        assert result["NEBUKO_LANE_ACTION"] == "move_to_project"


class TestRefusalOnlyForRunningComments:
    def test_plain_comment_on_a_personal_thread_gets_no_reply(self, workspace, monkeypatch):
        _project_comment(monkeypatch, workspace, author="bob", prev=_user_repo_state("alice"))
        result = _resolve("Thanks, this looks good to me.")

        assert result.get("COMMAND_ERROR") != "true"
        assert "--personal" not in result.get("REASON", "")
        assert "LANE_NOTICE" not in result or result["LANE_NOTICE"] == ""

    def test_agent_mention_without_the_flag_is_refused(self, workspace, monkeypatch):
        _project_comment(monkeypatch, workspace, author="alice", prev=_user_repo_state("alice"))
        result = _resolve("@@dev please check the diff")

        assert result["SKIP_AGENT"] == "true"
        assert result["COMMAND_ERROR"] == "true"
        assert "--personal" in result["REASON"] and "--project" in result["REASON"]

    def test_flagless_command_on_a_personal_thread_is_refused(self, workspace, monkeypatch):
        _project_comment(monkeypatch, workspace, author="alice", prev=_user_repo_state("alice"))
        result = _resolve("/nebuko approve gate")

        assert result["COMMAND_ERROR"] == "true"
        assert "--personal" in result["REASON"]


class TestPersonalCheckoutAndSecrets:
    def test_missing_checkout_is_named_in_the_run_notice(self, workspace, monkeypatch):
        _personal_run(monkeypatch, workspace, owner="alice", actor="alice")
        (workspace / ".nebuko-personal").rename(workspace.parent / "moved")
        result = _resolve("/nebuko run workflow --name default --personal --config agent-config-openrouter.yaml")

        assert ".nebuko-personal" in result["CHECKOUT_NOTICE"]
        assert "shipped defaults" in result["CHECKOUT_NOTICE"]

    def test_present_checkout_has_no_checkout_notice(self, workspace, monkeypatch):
        _personal_run(monkeypatch, workspace, owner="alice", actor="alice")
        result = _resolve("/nebuko run workflow --name default --personal --config agent-config-openrouter.yaml")

        assert not result.get("CHECKOUT_NOTICE")

    def test_missing_personal_secret_names_the_config_and_secret(self, workspace, monkeypatch):
        _personal_run(monkeypatch, workspace, owner="alice", actor="alice")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = _resolve("/nebuko run workflow --name default --personal --config agent-config-openrouter.yaml")

        message = result.get("REASON") or result.get("config_command_error") or ""
        assert "config agent-config-openrouter.yaml needs secret OPENROUTER_API_KEY" in message
        assert "not available in the personal lane" in message

    def test_personal_lane_never_reads_the_project_agents_dir(self, workspace, monkeypatch):
        project_agents = workspace / ".nebuko" / "agents"
        project_agents.mkdir(parents=True)
        (project_agents / "project-only.yaml").write_text("name: project-only\n")
        _personal_run(monkeypatch, workspace, owner="alice", actor="alice")
        out = rc._agent_config_selection(
            "", None, requested_config="project-only.yaml", personal_lane=True
        )
        assert "project-only" not in str(out.get("agent_config_path", ""))
        assert "config_command_error" in out or out.get("agent_config_layer") != "project"


class TestHandoffToTheRun:
    """The decision reaches the child run's environment through the resolved overlay."""

    def test_takeover_overlay_is_read_by_the_state_writer(self, workspace, monkeypatch):
        from nebuko.engine.run_loop import resolved_env_overlay

        _personal_run(monkeypatch, workspace, owner="bob", actor="bob", prev=_user_repo_state("alice"))
        overlay = resolved_env_overlay(_resolve("/nebuko run workflow --name default --personal"))

        assert lane_binding_from_env(overlay, "bob") == (USER_REPO_LANE, "bob")

    def test_move_back_overlay_records_the_shared_lane(self, workspace, monkeypatch):
        from nebuko.engine.run_loop import resolved_env_overlay

        _project_comment(monkeypatch, workspace, author="alice", prev=_user_repo_state("alice"))
        overlay = resolved_env_overlay(_resolve("/nebuko run workflow --name default --project"))

        assert lane_binding_from_env(overlay, None) == ("shared", None)
