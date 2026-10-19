"""``run_agent`` wires this step's skills into the Agent (issue #955).

Discover-mode skills must reach the SDK as ``AgentContext.skills`` (which renders
the name + summary catalog and keeps the body out of the prompt) and must pull in
the SDK's ``invoke_skill`` retrieval tool.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resolve_config.features.skill_context.skill_context import (
    AGENT_SKILL_SPECS_ENV,
    SKILLS_DIR_ENV,
    STEP_SKILL_SPECS_ENV,
    specs_to_env,
)

SKILLS_DIR = Path(__file__).resolve().parents[2] / "defaults" / "skills"


def _make_conversation(status_after: str = "finished") -> MagicMock:
    convo = MagicMock()

    async def _arun():
        convo.execution_status = SimpleNamespace(value=status_after)
        return None

    convo.arun = _arun
    convo.interrupt = MagicMock()
    convo.send_message = MagicMock()
    return convo


def _run_agent(monkeypatch, env: dict[str, str]):
    """Drive ``run_agent`` with a mocked SDK and return the Agent call kwargs."""
    from config import SkillSpec

    monkeypatch.setenv("AGENT_SKILL_SPECS", specs_to_env(SkillSpec.parse_all(env.get("agent"))))
    monkeypatch.setenv(
        "STEP_SKILL_SPECS", specs_to_env(SkillSpec.parse_all(env.get("step")))
    )
    monkeypatch.setenv(SKILLS_DIR_ENV, str(SKILLS_DIR))

    from agent_script.features.run_agent import run_agent as module

    with patch.object(module, "Conversation") as conversation_cls, patch.object(
        module, "Agent"
    ) as agent_cls, patch.object(module, "LLM"), patch.object(
        module, "resolve_timeout_budget"
    ) as budget, patch.object(module, "build_condenser", return_value=None):
        budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        conversation_cls.return_value = _make_conversation()
        module.run_agent(llm_config={}, prompt="do the thing")

    return agent_cls.call_args.kwargs


def test_discoverable_skills_reach_the_agent_context(monkeypatch):
    kwargs = _run_agent(monkeypatch, {"agent": [{"discover": ["complexity"]}]})

    agent_context = kwargs["agent_context"]
    assert [skill.name for skill in agent_context.skills] == ["complexity"]
    suffix = agent_context.get_system_message_suffix() or ""
    assert "<name>complexity</name>" in suffix
    assert "Complexity is governed" not in suffix


def test_discoverable_skills_attach_the_invoke_tool(monkeypatch):
    kwargs = _run_agent(monkeypatch, {"agent": [{"discover": ["complexity"]}]})

    assert "InvokeSkillTool" in kwargs["include_default_tools"]


def test_step_skills_are_merged_with_the_agent_roster(monkeypatch):
    kwargs = _run_agent(
        monkeypatch,
        {
            "agent": [{"inject": ["planning"]}],
            "step": [{"discover": ["stub-only"]}],
        },
    )

    assert [skill.name for skill in kwargs["agent_context"].skills] == ["stub-only"]


def test_inject_only_skills_leave_the_tool_list_alone(monkeypatch):
    kwargs = _run_agent(monkeypatch, {"agent": [{"inject": ["planning"]}]})

    assert kwargs["agent_context"].skills == []
    assert "InvokeSkillTool" not in kwargs["include_default_tools"]


def test_no_skills_keeps_the_plain_team_context(monkeypatch):
    kwargs = _run_agent(monkeypatch, {})

    agent_context = kwargs["agent_context"]
    assert agent_context is None or agent_context.skills == []


@pytest.mark.parametrize("env_var", [AGENT_SKILL_SPECS_ENV, STEP_SKILL_SPECS_ENV])
def test_a_malformed_spec_payload_never_breaks_the_run(monkeypatch, env_var):
    from agent_script.features.run_agent import run_agent as module

    # The other channel must be empty too: an ambient value (e.g. under a live
    # agent run) would legitimately attach the invoke tool and mask the point.
    for name in (AGENT_SKILL_SPECS_ENV, STEP_SKILL_SPECS_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_var, "{not json")
    monkeypatch.setenv(SKILLS_DIR_ENV, str(SKILLS_DIR))

    with patch.object(module, "Conversation") as conversation_cls, patch.object(
        module, "Agent"
    ) as agent_cls, patch.object(module, "LLM"), patch.object(
        module, "resolve_timeout_budget"
    ) as budget, patch.object(module, "build_condenser", return_value=None):
        budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        conversation_cls.return_value = _make_conversation()
        module.run_agent(llm_config={}, prompt="do the thing")

    assert "InvokeSkillTool" not in agent_cls.call_args.kwargs["include_default_tools"]
