"""Tests for resolve_config.py."""

import tempfile
import os

import pytest

from resolve_config import (
    extract_agent_run_config,
    is_bot_comment,
    extract_mention_agent,
    extract_workflow_command,
    resolve_config,
    resolve_next_agent_from_workflow,
    count_consecutive_same_agent,
)
from resolve_workflow import TASK_TO_PROMPT


import unittest


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


class TestExtractWorkflowCommand:
    """Test /workflow command extraction from trigger body."""

    def test_valid_workflow_command(self):
        result = extract_workflow_command("/workflow default\nSome description")
        assert result == "default"

    def test_valid_workflow_command_with_spaces(self):
        result = extract_workflow_command("/workflow  review-only")
        assert result == "review-only"

    def test_no_name_after_workflow(self):
        result = extract_workflow_command("/workflow\nSome text")
        assert result is None

    def test_workflow_not_on_first_line(self):
        result = extract_workflow_command("Some intro text\n/workflow default")
        assert result is None

    def test_slash_workflow_in_middle_of_text(self):
        result = extract_workflow_command("Hello there /workflow default")
        assert result is None

    def test_empty_body(self):
        result = extract_workflow_command("")
        assert result is None

    def test_similar_but_not_workflow(self):
        result = extract_workflow_command("/workflows default")
        assert result is None

    def test_workflow_with_additional_text_same_line(self):
        result = extract_workflow_command("/workflow demo-composition — please run this")
        assert result == "demo-composition"


class TestResolveConfigWithWorkflowCommand:
    """Test resolve_config with /workflow command."""

    def _setup_dirs_with_workflows(self):
        """Create temp dirs with actual workflow files."""
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        # Create dummy prompt files
        for name in TASK_TO_PROMPT.values():
            path = os.path.join(prompt_base, name)
            with open(path, "w") as f:
                f.write(f"# Prompt for {name}")
        # Create workflow YAMLs
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(
                "name: default\n"
                "description: Standard workflow\n"
                "trigger:\n"
                "  agents: [genius]\n"
                "steps:\n"
                "  - name: analyze\n"
                "    agent: genius\n"
                "    next: null\n"
            )
        with open(os.path.join(workflows_dir, "review-only.yaml"), "w") as f:
            f.write(
                "name: review-only\n"
                "description: Review only\n"
                "trigger:\n"
                "  agents: [review]\n"
                "steps:\n"
                "  - name: review\n"
                "    agent: review\n"
                "    next: null\n"
            )
        return prompt_base, workflows_dir

    def test_workflow_default_resolves_genius(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/workflow default\nPlease analyze this issue."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "genius"
        assert result["ROLE_NAME"] == "genius"
        assert result["WORKFLOW_NAME"] == "default"
        assert result["SKIP_AGENT"] == "false"

    def test_workflow_review_only_resolves_review(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/workflow review-only\nReview my PR please."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "review"
        assert result["ROLE_NAME"] == "review"
        assert result["WORKFLOW_NAME"] == "review-only"

    def test_workflow_nonexistent_falls_through_mentions(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/workflow nonexistent\n@@genius help me"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # Nonexistent workflow -> falls through to @@mention
        assert result["TASK"] == "genius"
        assert result["ROLE_NAME"] == "genius"
        assert result["WORKFLOW_NAME"] == ""
        assert result["SKIP_AGENT"] == "false"

    def test_workflow_malformed_name_falls_through_mentions(self):
        """Workflow names with special chars are rejected and fall through."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = '/workflow "../etc/passwd"\n@@dev help'

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == ""
        assert result["SKIP_AGENT"] == "false"

    def test_workflow_empty_agents_falls_through_mentions(self):
        """Workflow with no trigger agents falls through to @@mention."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        # Create a workflow with empty agents
        with open(os.path.join(workflows_dir, "empty-agents.yaml"), "w") as f:
            f.write(
                "name: empty-agents\n"
                "description: No agents\n"
                "trigger:\n"
                "  agents: []\n"
                "steps:\n"
                "  - name: review\n"
                "    agent: review\n"
                "    next: null\n"
            )
        body = "/workflow empty-agents\n@@ctx gather context"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "ctx"
        assert result["ROLE_NAME"] == "ctx"
        assert result["WORKFLOW_NAME"] == ""
        assert result["SKIP_AGENT"] == "false"

    def test_workflow_not_on_first_line_ignored(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "Some intro text\n/workflow default"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # Not on first line, no @@mention -> skip
        assert result["SKIP_AGENT"] == "true"

    def test_workflow_not_on_first_line_falls_through_mention(self):
        """When /workflow not on first line but @@mention present, use mention."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "Some intro text\n/workflow default\n@@dev please help"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "dev"
        assert result["ROLE_NAME"] == "dev"
        assert result["WORKFLOW_NAME"] == ""

    def test_agent_run_config_takes_precedence_over_workflow(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = (
            "/workflow review-only\n"
            '<agentRunConfig>{"task": "dev", "complexity": "low"}</agentRunConfig>'
        )

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # agentRunConfig takes precedence
        assert result["TASK"] == "dev"
        assert result["ROLE_NAME"] == "dev"
        assert result["COMPLEXITY"] == "low"

    def test_workflow_command_takes_precedence_over_mention(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/workflow default\n@@review please also review"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # /workflow takes priority over @@mention
        assert result["TASK"] == "genius"
        assert result["WORKFLOW_NAME"] == "default"

    def test_workflow_bot_comment_skips(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "<!-- bot-agent-dev -->\n/workflow default"

        result = resolve_config(body, prompt_base, workflows_dir)

        # Bot comments should skip even with /workflow
        assert result["SKIP_AGENT"] == "true"



class TestResolveNextAgentFromWorkflow(unittest.TestCase):
    """Tests for resolve_next_agent_from_workflow function."""

    def test_task_hint_overrides_workflow(self):
        result = resolve_next_agent_from_workflow("dev", "default", "analyze", "workflows")
        self.assertEqual(result, "dev")

    def test_resolves_next_from_default_workflow(self):
        result = resolve_next_agent_from_workflow(None, "default", "analyze", "workflows")
        self.assertEqual(result, "ctx")

    def test_returns_none_for_missing_workflow(self):
        result = resolve_next_agent_from_workflow(None, "nonexistent", "analyze", "workflows")
        self.assertIsNone(result)

    def test_returns_none_for_empty_workflow_name(self):
        result = resolve_next_agent_from_workflow(None, "", "analyze", "workflows")
        self.assertIsNone(result)

    def test_returns_none_for_terminal_step(self):
        # "review" is the last step in default.yaml (next: null)
        result = resolve_next_agent_from_workflow(None, "default", "review", "workflows")
        self.assertIsNone(result)


class TestCountConsecutiveSameAgent(unittest.TestCase):
    """Tests for count_consecutive_same_agent function."""

    def test_empty_history(self):
        from state_manager import HistoryEntry
        count = count_consecutive_same_agent([], "dev")
        self.assertEqual(count, 0)

    def test_single_match(self):
        from state_manager import HistoryEntry
        history = [HistoryEntry(step="s", agent="dev")]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 1)

    def test_mixed_agents(self):
        from state_manager import HistoryEntry
        history = [
            HistoryEntry(step="s1", agent="genius"),
            HistoryEntry(step="s2", agent="dev"),
            HistoryEntry(step="s3", agent="dev"),
        ]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 2)

    def test_different_agent(self):
        from state_manager import HistoryEntry
        history = [HistoryEntry(step="s1", agent="genius")]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 0)
