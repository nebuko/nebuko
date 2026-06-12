"""Tests for resolve_config.py."""

import tempfile
import os

import pytest

from resolve_config import (
    extract_agent_run_config,
    is_bot_comment,
    extract_mention_agent,
    resolve_config,
)
from resolve_workflow import TASK_TO_PROMPT


class TestExtractAgentRunConfig:
    """Test JSON extraction from trigger body text."""

    def test_extract_structured_block(self):
        body = 'Some text\n<agentRunConfig>{"task": "dev", "complexity": "high"}</agentRunConfig>\nMore text'
        result = extract_agent_run_config(body)
        assert result == {"task": "dev", "complexity": "high"}

    def test_extract_multiline_json_block(self):
        body = '<agentRunConfig>\n{"task": "genius",\n "reasoning": "medium"}\n</agentRunConfig>'
        result = extract_agent_run_config(body)
        assert result["task"] == "genius"
        assert result["reasoning"] == "medium"

    def test_extract_trailing_json_fallback(self):
        body = "Please fix this @@dev {\"task\": \"dev\", \"complexity\": \"low\"}"
        result = extract_agent_run_config(body)
        assert result == {"task": "dev", "complexity": "low"}

    def test_no_config_returns_none(self):
        result = extract_agent_run_config("Just plain text with no config")
        assert result is None

    def test_invalid_json_in_block_returns_none(self):
        body = "<agentRunConfig>{invalid json}</agentRunConfig>"
        result = extract_agent_run_config(body)
        assert result is None


class TestIsBotComment:
    """Test bot comment detection."""

    def test_bot_marker_at_start(self):
        body = "<!-- bot-agent-dev -->\n@@genius please analyze"
        assert is_bot_comment(body) is True

    def test_bot_marker_in_first_five_lines(self):
        lines = ["line 1", "line 2", "line 3", "<!-- bot-agent-ctx -->", "content"]
        body = "\n".join(lines)
        assert is_bot_comment(body) is True

    def test_no_bot_marker(self):
        body = "Regular human comment without any markers"
        assert is_bot_comment(body) is False

    def test_empty_body(self):
        assert is_bot_comment("") is False


class TestExtractMentionAgent:
    """Test @@mention agent extraction."""

    def test_single_mention(self):
        result = extract_mention_agent("Please @@dev fix this")
        assert result == "dev"

    def test_last_mention_wins(self):
        result = extract_mention_agent("@@genius analyze then @@dev implement")
        assert result == "dev"

    def test_no_mention(self):
        result = extract_mention_agent("No mentions here")
        assert result is None

    def test_all_agent_types(self):
        for agent in ("genius", "ctx", "dev", "review", "issue", "issue-reviewer"):
            result = extract_mention_agent(f"Hello @@{agent} help me")
            assert result == agent


class TestResolveConfig:
    """Test full resolve_config function."""

    def _setup_dirs(self):
        """Create temp prompt_base and workflows_dir."""
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        # Create a dummy prompt file
        for name in TASK_TO_PROMPT.values():
            path = os.path.join(prompt_base, name)
            with open(path, "w") as f:
                f.write(f"# Prompt for {name}")
        return prompt_base, workflows_dir

    def test_json_based_dev_resolution(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = '<agentRunConfig>{"task": "dev", "complexity": "high"}</agentRunConfig>'

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["TASK"] == "dev"
        assert result["ROLE_NAME"] == "dev"
        assert result["COMPLEXITY"] == "high"
        assert result["AGENT_SELECTED"] == "true"
        assert result["SKIP_AGENT"] == "false"

    def test_human_mention_fallback(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = "@@genius please create a plan"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "genius"
        assert result["ROLE_NAME"] == "genius"
        assert result["COMPLEXITY"] == "N/A"
        assert result["LLM_REASONING"] == "none"

    def test_bot_without_config_skips(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = "<!-- bot-agent-dev -->\nSome comment without JSON"

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["SKIP_AGENT"] == "true"

    def test_empty_task_skips(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = "No valid task here"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["SKIP_AGENT"] == "true"

    def test_complexity_normalization(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = '<agentRunConfig>{"task": "dev", "complexity": "HIGH"}</agentRunConfig>'

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["COMPLEXITY"] == "high"

    def test_complexity_defaults_to_medium(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = '<agentRunConfig>{"task": "dev"}</agentRunConfig>'

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["COMPLEXITY"] == "medium"

    def test_all_config_fields_exported(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = (
            '<agentRunConfig>{"task": "dev", "complexity": "low", '
            '"reasoning": "high", "temperature": "0.5", "round": "3", '
            '"model": "custom-model", "skills": "python", '
            '"top_p": "0.9", "top_k": "50", "repetition_penalty": "1.2"}</agentRunConfig>'
        )

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["CONFIG_MODEL"] == "custom-model"
        assert result["CONFIG_TEMPERATURE"] == "0.5"
        assert result["AGENT_ROUND"] == "3"
        assert result["AGENT_SKILLS"] == "python"
        assert result["CONFIG_TOP_P"] == "0.9"
        assert result["CONFIG_TOP_K"] == "50"
        assert result["CONFIG_REPETITION_PENALTY"] == "1.2"
        assert result["LLM_REASONING"] == "high"

    def test_ctx_complexity_handling(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = '<agentRunConfig>{"task": "ctx", "complexity": "low"}</agentRunConfig>'

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["COMPLEXITY"] == "low"

    def test_workflow_routing_integration(self):
        """When workflow is specified, resolve_workflow handles routing."""
        prompt_base, workflows_dir = self._setup_dirs()
        body = '<agentRunConfig>{"task": "genius", "workflow": "default", "step": "analyze"}</agentRunConfig>'

        result = resolve_config(body, prompt_base, workflows_dir)

        # Workflow file doesn't exist so it falls back to direct mapping
        assert result["ROLE_NAME"] == "genius"
        assert result["WORKFLOW_NAME"] == "default"
        assert result["SKIP_AGENT"] == "false"
