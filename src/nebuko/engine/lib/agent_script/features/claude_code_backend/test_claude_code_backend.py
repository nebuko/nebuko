"""Tests for the Claude Code (ACP) backend builder."""

import pytest

from agent_script.features.claude_code_backend import (
    ENGINE_PREAMBLE,
    acp_model_from_model_path,
    build_claude_code_agent,
    is_claude_code_agent,
    with_engine_preamble,
)


@pytest.mark.parametrize(
    ("model_path", "alias"),
    [
        ("anthropic/sonnet", "sonnet"),
        ("claude-code/opus[1m]", "opus[1m]"),
        ("anthropic/claude-opus-5", "claude-opus-5"),
        ("haiku", "haiku"),
        ("anthropic/", None),
        ("", None),
        (None, None),
    ],
)
def test_the_alias_is_the_last_segment_of_the_model_path(model_path, alias):
    assert acp_model_from_model_path(model_path) == alias


def test_the_preamble_leads_the_role_prompt():
    prompt = with_engine_preamble("Implement the issue.")

    assert prompt.startswith(ENGINE_PREAMBLE)
    assert prompt.endswith("Implement the issue.")
    assert "finalize" in ENGINE_PREAMBLE


class TestBuildClaudeCodeAgent:
    def test_launches_the_registry_command_with_the_alias(self):
        agent = build_claude_code_agent("anthropic/sonnet")

        assert is_claude_code_agent(agent)
        assert agent.acp_server == "claude-code"
        assert agent.acp_model == "sonnet"
        assert any("claude-agent-acp" in part for part in agent.acp_command)

    def test_no_model_leaves_claude_code_on_its_default(self):
        assert build_claude_code_agent(None).acp_model is None

    def test_owns_no_sdk_tools_or_condenser(self):
        agent = build_claude_code_agent("anthropic/sonnet")

        assert agent.tools == []
        assert agent.condenser is None

    def test_a_plain_object_is_not_a_claude_code_agent(self):
        assert not is_claude_code_agent(object())
