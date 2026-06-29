#!/usr/bin/env python3
"""Unit tests for trigger_parser module."""

import pytest
from trigger_parser import (
    parse_trigger,
    _is_bot_comment,
    _extract_workflow_command,
    _extract_force_next_command,
    _extract_mention_agent,
    _extract_agent_run_config,
)


class TestParseTrigger:
    """Test the main parse_trigger orchestrator."""

    def test_empty_trigger(self):
        hints = parse_trigger("")
        assert hints.is_bot_comment is False
        assert hints.workflow_name is None
        assert hints.step_name is None
        assert hints.agent_name is None
        assert hints.force_next_step is None
        assert hints.complexity == "medium"

    def test_multiple_hints_combined(self):
        body = (
            "@@genius\n"
            "This needs a complex fix @@dev\n"
            "<agentRunConfig>{\"status\": \"advance\", \"workflow\": \"spec-driven-design\", \"step\": \"implement\"}</agentRunConfig>"
        )
        hints = parse_trigger(body)
        assert hints.is_bot_comment is False
        assert hints.agent_name == "dev"

    def test_bot_detection(self):
        body = "<!-- bot-agent-genius -->\nSome content"
        hints = parse_trigger(body)
        assert hints.is_bot_comment is True


class TestIsBotComment:
    """Test bot comment detection."""

    def test_bot_marker_in_first_lines(self):
        body = "<!-- bot-agent-genius -->\nContent here"
        assert _is_bot_comment(body) is True

    def test_bot_marker_with_spaces(self):
        body = "  \n\n<!-- bot-agent-dev -->\nMore content"
        assert _is_bot_comment(body) is True

    def test_no_bot_marker(self):
        body = "Hello world, no bot here"
        assert _is_bot_comment(body) is False

    def test_bot_marker_beyond_first_five_lines(self):
        body = "\n".join(["line " + str(i) for i in range(10)]) + "\n<!-- bot-agent- -->"
        assert _is_bot_comment(body) is False

    def test_empty_string(self):
        assert _is_bot_comment("") is False


class TestExtractWorkflowCommand:
    """Test /workflow command extraction."""

    def test_workflow_command_with_name(self):
        body = "/workflow spec-driven-design\nSome content"
        assert _extract_workflow_command(body) == "spec-driven-design"

    def test_workflow_command_only(self):
        body = "/workflow review"
        assert _extract_workflow_command(body) == "review"

    def test_no_workflow_command(self):
        body = "Hello world"
        assert _extract_workflow_command(body) is None

    def test_workflow_in_second_line_ignored(self):
        body = "Some intro\n/workflow test"
        assert _extract_workflow_command(body) is None

    def test_workflow_command_with_trailing_text(self):
        body = "/workflow test extra stuff"
        # Should only match the first word after /workflow
        assert _extract_workflow_command(body) == "test"


class TestExtractForceNextCommand:
    """Test /workflow force-next-step command extraction."""

    def test_force_next_step_no_target(self):
        body = "/workflow force-next-step"
        result = _extract_force_next_command(body)
        assert result == "force-next-step"

    def test_force_next_step_with_target(self):
        body = "/workflow force-next-step implement"
        result = _extract_force_next_command(body)
        assert result == "implement"

    def test_force_next_step_with_complex_target(self):
        body = "/workflow force-next-step code-review"
        result = _extract_force_next_command(body)
        assert result == "code-review"

    def test_no_force_next_command(self):
        body = "Just a regular message"
        assert _extract_force_next_command(body) is None

    def test_workflow_command_not_force_next(self):
        body = "/workflow spec-driven-design"
        assert _extract_force_next_command(body) is None


class TestExtractMentionAgent:
    """Test @@mention agent extraction."""

    def test_single_mention(self):
        body = "Please help @@genius with this"
        assert _extract_mention_agent(body) == "genius"

    def test_last_mention_wins(self):
        body = "Ask @@ctx first then @@genius"
        assert _extract_mention_agent(body) == "genius"

    def test_no_mention(self):
        body = "No mentions here"
        assert _extract_mention_agent(body) is None

    def test_invalid_agent_name_ignored(self):
        body = "@@unknown is not valid"
        assert _extract_mention_agent(body) is None

    def test_all_valid_agents(self):
        for agent in ["issue-reviewer", "genius", "ctx", "dev", "review", "issue"]:
            body = f"Call @@{agent}"
            assert _extract_mention_agent(body) == agent


class TestExtractAgentRunConfig:
    """Test agentRunConfig XML extraction."""

    def test_structured_block(self):
        body = 'Some text <agentRunConfig>{"status": "advance"}</agentRunConfig> more text'
        result = _extract_agent_run_config(body)
        assert result == {"status": "advance"}

    def test_multiline_json_block(self):
        body = """Text
<agentRunConfig>
{"status": "advance",
 "complexity": "high"}
</agentRunConfig>
More text"""
        result = _extract_agent_run_config(body)
        assert result == {"status": "advance", "complexity": "high"}

    def test_trailing_json_fallback(self):
        body = '{"task": "dev", "complexity": "low"}'
        result = _extract_agent_run_config(body)
        assert result == {"task": "dev", "complexity": "low"}

    def test_no_config(self):
        body = "Plain text without any config"
        assert _extract_agent_run_config(body) is None

    def test_malformed_json(self):
        body = '<agentRunConfig>{"invalid json}</agentRunConfig>'
        assert _extract_agent_run_config(body) is None

    def test_trailing_json_invalid_task(self):
        body = '{"task": "unknown", "complexity": "low"}'
        assert _extract_agent_run_config(body) is None

    def test_xml_takes_precedence_over_trailing_json(self):
        body = (
            '<agentRunConfig>{"status": "hold"}</agentRunConfig> '
            '{"task": "dev"}'
        )
        result = _extract_agent_run_config(body)
        assert result == {"status": "hold"}
