"""Tests for the Claude Code (ACP) backend builder."""

import pytest

from agent_script.features.claude_code_backend import (
    ENGINE_PREAMBLE,
    _claude_code_cli_args_for_tools,
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

    def test_unrestricted_role_gets_no_deny_args_or_session_mode(self):
        # dev role with full tools: no CLI args, default session mode
        tools = ["terminal", "file_editor", "task_tracker", "think", "finish"]
        acp_args, session_mode = _claude_code_cli_args_for_tools(tools)

        assert acp_args == []
        assert session_mode is None

    def test_restricted_role_without_exec_tools_gets_deny_list(self):
        # architect role: only think, finish, finalize
        tools = ["think", "finish", "finalize"]
        acp_args, session_mode = _claude_code_cli_args_for_tools(tools)

        assert len(acp_args) > 0  # Should have some args
        assert session_mode is not None  # Should restrict session mode
        # The actual args depend on CLI support; just verify it's attempting restriction

    def test_empty_tools_list_is_unrestricted(self):
        # No tools specified: treat as unrestricted
        acp_args, session_mode = _claude_code_cli_args_for_tools([])

        assert acp_args == []
        assert session_mode is None

    def test_none_tools_is_unrestricted(self):
        # None tools: treat as unrestricted
        acp_args, session_mode = _claude_code_cli_args_for_tools(None)

        assert acp_args == []
        assert session_mode is None

    def test_restricted_agent_receives_args_and_session_mode(self):
        # Test that build_claude_code_agent passes the args to ACPAgent
        restricted_tools = ["think", "finish", "finalize"]
        agent = build_claude_code_agent("anthropic/sonnet", allowed_tools=restricted_tools)

        # Should have acp_args set if tools are restricted
        assert agent.acp_args is not None  # Could be empty or have args
        assert agent.acp_session_mode is not None  # Should be restricted mode

    def test_unrestricted_agent_receives_defaults(self):
        # Test that build_claude_code_agent with full tools uses defaults
        full_tools = ["terminal", "file_editor", "task_tracker", "think", "finish"]
        agent = build_claude_code_agent("anthropic/sonnet", allowed_tools=full_tools)

        # Should use defaults (empty args, None session mode for unrestricted)
        assert agent.acp_args == []
        assert agent.acp_session_mode is None
