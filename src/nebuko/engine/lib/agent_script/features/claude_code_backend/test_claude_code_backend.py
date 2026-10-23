"""Tests for the Claude Code (ACP) backend builder."""

import pytest

from agent_script.features.claude_code_backend import (
    ENGINE_PREAMBLE,
    acp_model_from_model_path,
    build_claude_code_agent,
    is_claude_code_agent,
    with_engine_preamble,
)
from agent_script.features.claude_code_backend.claude_code_backend import (
    _claude_code_cli_args_for_tools,
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


class TestToolEnforcement:
    """Tests for tool restriction enforcement on Claude Code roles."""

    @pytest.mark.parametrize(
        ("allowed_tools", "expected_args", "expected_session_mode"),
        [
            # Unrestricted roles (have exec tools) → no enforcement
            (["terminal", "file_editor", "think"], [], None),
            (["terminal", "think", "finish"], [], None),
            (["file_editor", "task_tracker"], [], None),
            # Restricted roles (no exec tools) → enforcement enabled
            (["think", "finish", "finalize"], ["--disallowedTools=terminal,file_editor"], "restrictedConsole"),
            (["terminal_batch", "think"], ["--disallowedTools=terminal,file_editor"], "restrictedConsole"),
            # Edge cases
            (None, [], None),  # None treated as unrestricted
            ([], [], None),  # Empty list treated as unrestricted
        ],
    )
    def test_cli_args_match_tool_allowlist(self, allowed_tools, expected_args, expected_session_mode):
        args, session_mode = _claude_code_cli_args_for_tools(allowed_tools)
        assert args == expected_args
        assert session_mode == expected_session_mode

    def test_unrestricted_role_with_terminal_receives_no_args(self):
        args, session_mode = _claude_code_cli_args_for_tools(["terminal", "think"])
        assert args == []
        assert session_mode is None

    def test_restricted_role_without_exec_tools_receives_enforcement(self):
        args, session_mode = _claude_code_cli_args_for_tools(["think", "finish", "finalize"])
        assert "--disallowedTools=terminal,file_editor" in args
        assert session_mode == "restrictedConsole"

    def test_agent_with_restricted_tools_gets_enforcement_args(self):
        agent = build_claude_code_agent(
            "anthropic/sonnet",
            allowed_tools=["think", "finish", "finalize"],
        )
        assert is_claude_code_agent(agent)
        # Check that enforcement args are in the command
        assert any("--disallowedTools" in part for part in agent.acp_command)

    def test_agent_with_unrestricted_tools_has_no_enforcement_args(self):
        agent = build_claude_code_agent(
            "anthropic/sonnet",
            allowed_tools=["terminal", "file_editor", "think"],
        )
        assert is_claude_code_agent(agent)
        # Check that no enforcement args are in the command
        assert not any("--disallowedTools" in part for part in agent.acp_command)

    def test_agent_with_none_allowed_tools_has_no_enforcement(self):
        agent = build_claude_code_agent("anthropic/sonnet", allowed_tools=None)
        assert is_claude_code_agent(agent)
        assert not any("--disallowedTools" in part for part in agent.acp_command)
