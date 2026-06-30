"""Tests for resolve_config.py."""

import tempfile
import os
from pathlib import Path

import pytest

from resolve_config import (
    is_bot_comment,
    extract_mention_agent,
    extract_workflow_command,
    extract_force_next_command,
    resolve_config,
    resolve_next_agent_from_workflow,
    count_consecutive_same_agent,
)
from resolve_workflow import TASK_TO_PROMPT


import unittest


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
        # Use dispatch embedded in WORKFLOW_STATE (XML path removed)
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 1, "dispatch": {"task_hint": "dev", "complexity": "high"}}\n'
            "<!-- WORKFLOW_STATE_END -->"
        )

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
        # Use @@mention path for complexity normalization test (human path)
        body = "@@dev please fix this bug"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["COMPLEXITY"] == "medium"

    def test_complexity_defaults_to_medium(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = "@@dev implement the feature"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["COMPLEXITY"] == "medium"

    def test_all_config_fields_exported(self):
        prompt_base, workflows_dir = self._setup_dirs()
        # Use dispatch in WORKFLOW_STATE for all fields test
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 1, "dispatch": {"task_hint": "dev", "complexity": "low", '
            '"reasoning": "high", "temperature": 0.5, "model": "custom-model", '
            '"top_p": 0.9, "top_k": 50, "repetition_penalty": 1.2}}\n'
            "<!-- WORKFLOW_STATE_END -->"
        )

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["CONFIG_MODEL"] == "custom-model"
        assert result["CONFIG_TEMPERATURE"] == "0.5"
        assert result["CONFIG_TOP_P"] == "0.9"
        # top_k uses Float in Dispatch model; str(Float(50.0)) -> "50.0"
        assert result["CONFIG_TOP_K"] == "50.0"
        assert result["CONFIG_REPETITION_PENALTY"] == "1.2"
        assert result["LLM_REASONING"] == "high"

    def test_ctx_complexity_handling(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = "@@ctx gather context about the codebase"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["COMPLEXITY"] == "medium"

    def test_workflow_routing_integration(self):
        """When workflow is specified, resolve_workflow handles routing."""
        prompt_base, workflows_dir = self._setup_dirs()
        # /workflow without a valid workflow file and no @@mention → SKIP_AGENT
        body = "/workflow nonexistent\nPlease analyze this issue."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # Falls through to @@mention (none found) → skips
        assert result["SKIP_AGENT"] == "true"


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

    def test_step_skills_passed_through_from_workflow(self):
        """STEP_SKILLS from workflow resolver are passed through in resolve_config output."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        # Create a workflow with skills on a step
        with open(os.path.join(workflows_dir, "with-skills.yaml"), "w") as f:
            f.write(
                "name: with-skills\n"
                "description: Workflow with skills\n"
                "trigger:\n"
                "  agents: [dev]\n"
                "steps:\n"
                "  - name: implement\n"
                "    agent: dev\n"
                "    skill: [python, rust]\n"
                "    next: null\n"
            )
        # Use dispatch in WORKFLOW_STATE to specify workflow/step (XML removed)
        # Workflow/step must be on the dispatch object for resolve_workflow_routing to pick them up
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 1, "workflow": "with-skills", "step": "implement", '
            '"dispatch": {"task_hint": "dev", "workflow": "with-skills", "step": "implement"}}\n'
            "<!-- WORKFLOW_STATE_END -->"
        )

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["ROLE_NAME"] == "dev"
        assert result["WORKFLOW_NAME"] == "with-skills"
        assert result["STEP_SKILLS"] == "python,rust"

    def test_step_skills_empty_when_not_defined(self):
        """STEP_SKILLS is empty when workflow step has no skill field."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/workflow default\nPlease analyze this issue."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["STEP_SKILLS"] == ""



class TestResolveNextAgentFromWorkflow(unittest.TestCase):
    """Tests for resolve_next_agent_from_workflow function."""

    @staticmethod
    def _real_workflows_dir():
        """Return the path to the real workflows/ directory at the repo root."""
        return str(Path(__file__).resolve().parents[2] / "workflows")

    def test_task_hint_overrides_workflow(self):
        result = resolve_next_agent_from_workflow("dev", "default", "analyze", self._real_workflows_dir())
        self.assertEqual(result, "dev")

    def test_resolves_next_from_default_workflow(self):
        result = resolve_next_agent_from_workflow(None, "default", "analyze", self._real_workflows_dir())
        self.assertEqual(result, "ctx")

    def test_returns_none_for_missing_workflow(self):
        result = resolve_next_agent_from_workflow(None, "nonexistent", "analyze", self._real_workflows_dir())
        self.assertIsNone(result)

    def test_returns_none_for_empty_workflow_name(self):
        result = resolve_next_agent_from_workflow(None, "", "analyze", self._real_workflows_dir())
        self.assertIsNone(result)

    def test_returns_none_for_terminal_step(self):
        # "review" is the last step in default.yaml (next: null)
        result = resolve_next_agent_from_workflow(None, "default", "review", self._real_workflows_dir())
        self.assertIsNone(result)


class TestCountConsecutiveSameAgent(unittest.TestCase):
    """Tests for count_consecutive_same_agent function."""

    def test_empty_history(self):
        from workflow_state import HistoryEntry
        count = count_consecutive_same_agent([], "dev")
        self.assertEqual(count, 0)

    def test_single_match(self):
        from workflow_state import HistoryEntry
        history = [HistoryEntry(step="s", agent="dev")]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 1)

    def test_mixed_agents(self):
        from workflow_state import HistoryEntry
        history = [
            HistoryEntry(step="s1", agent="genius"),
            HistoryEntry(step="s2", agent="dev"),
            HistoryEntry(step="s3", agent="dev"),
        ]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 2)

    def test_different_agent(self):
        from workflow_state import HistoryEntry
        history = [HistoryEntry(step="s1", agent="genius")]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 0)


class TestExtractForceNextCommand(unittest.TestCase):
    """Tests for extract_force_next_command function."""

    def test_basic_force_next_step(self):
        result = extract_force_next_command("/workflow force-next-step")
        self.assertEqual(result, ("force-next-step", None))

    def test_force_next_step_with_target(self):
        result = extract_force_next_command("/workflow force-next-step implement")
        self.assertEqual(result, ("force-next-step", "implement"))

    def test_force_next_step_with_multiline_input(self):
        result = extract_force_next_command("/workflow force-next-step implement\nSome description here")
        self.assertEqual(result, ("force-next-step", "implement"))

    def test_not_force_next_step(self):
        result = extract_force_next_command("/workflow default")
        self.assertIsNone(result)

    def test_empty_body(self):
        result = extract_force_next_command("")
        self.assertIsNone(result)

    def test_extra_text_after_target(self):
        result = extract_force_next_command("/workflow force-next-step implement some extra text")
        self.assertIsNone(result)

    def test_force_next_step_with_underscore_target(self):
        result = extract_force_next_command("/workflow force-next-step code_review")
        self.assertEqual(result, ("force-next-step", "code_review"))


class TestForceNextStepIntegration(unittest.TestCase):
    """Integration tests for /workflow force-next-step command."""

    @staticmethod
    def _default_workflow_yaml():
        return (
            "name: default\n"
            "description: Standard workflow\n"
            "trigger:\n"
            "  agents: [genius]\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: genius\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: ctx\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: genius\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    @staticmethod
    def _build_state_json(workflow="default", step="implement", parent_workflow=None, parent_workflow_step=None):
        import json as _json
        state = {
            "version": 1,
            "workflow": workflow,
            "step": step,
            "agent": "dev",
        }
        if parent_workflow:
            state["parent_workflow"] = parent_workflow
        if parent_workflow_step:
            state["parent_workflow_step"] = parent_workflow_step
        return f"<!-- WORKFLOW_STATE_START -->\n{_json.dumps(state)}\n<!-- WORKFLOW_STATE_END -->"

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(self._default_workflow_yaml())
        return prompt_base, workflows_dir

    def _resolve_with_state(self, body, state_json):
        prompt_base, workflows_dir = self._setup_dirs()
        full_body = body + "\n\n" + state_json
        return resolve_config(full_body, prompt_base, workflows_dir, is_bot=False)

    def test_force_next_step_auto_advance(self):
        state_json = self._build_state_json(workflow="default", step="implement")
        result = self._resolve_with_state("/workflow force-next-step", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "review")

    def test_force_next_step_with_explicit_target(self):
        state_json = self._build_state_json(workflow="default", step="analyze")
        result = self._resolve_with_state("/workflow force-next-step implement", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "dev")

    def test_force_next_step_no_workflow_state(self):
        prompt_base, workflows_dir = self._setup_dirs()
        result = resolve_config("/workflow force-next-step", prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("no workflow state found", result["REASON"])

    def test_force_next_step_terminal_step(self):
        state_json = self._build_state_json(workflow="default", step="review")
        result = self._resolve_with_state("/workflow force-next-step", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("terminal", result["REASON"])

    def test_force_next_step_invalid_target_step(self):
        state_json = self._build_state_json(workflow="default", step="analyze")
        result = self._resolve_with_state("/workflow force-next-step nonexistent", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("not found", result["REASON"])

    def test_force_next_step_path_traversal_workflow_name(self):
        state_json = self._build_state_json(workflow="../etc/passwd", step="hack")
        result = self._resolve_with_state("/workflow force-next-step", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("invalid workflow name", result["REASON"])

    def test_force_next_step_path_traversal_target_step(self):
        state_json = self._build_state_json(workflow="default", step="analyze")
        result = self._resolve_with_state("/workflow force-next-step ../../etc/passwd", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("invalid target step", result["REASON"])

    def test_force_next_step_invalid_workflow_file(self):
        state_json = self._build_state_json(workflow="nonexistent_workflow", step="analyze")
        result = self._resolve_with_state("/workflow force-next-step", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("not found", result["REASON"])

    def test_force_next_step_current_step_not_in_workflow(self):
        state_json = self._build_state_json(workflow="default", step="nonexistent_step")
        result = self._resolve_with_state("/workflow force-next-step", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("not found", result["REASON"])

    def test_force_next_step_preserves_parent_workflow(self):
        # Note: parent workflow fields are preserved by resolve_config.
        # Downstream resolve_workflow.py may reject if parent workflow YAML
        # doesn't exist — that's separate from force-next-step logic.
        state_json = self._build_state_json(workflow="default", step="implement")
        result = self._resolve_with_state("/workflow force-next-step", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["WORKFLOW_NAME"], "default")

    def test_force_next_step_jumps_to_context(self):
        state_json = self._build_state_json(workflow="default", step="analyze")
        result = self._resolve_with_state("/workflow force-next-step context", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "ctx")

    def test_force_next_step_next_step_does_not_exist_in_workflow(self):
        """When step.next references a step not in the workflow, return a clear error."""
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        # Malformed workflow: analyze.next points to 'nonexistent' which doesn't exist
        malformed_yaml = (
            "name: broken\n"
            "description: Broken workflow\n"
            "trigger:\n"
            "  agents: [genius]\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: genius\n"
            "    next: nonexistent\n"
            "  - name: context\n"
            "    agent: ctx\n"
            "    next: null\n"
        )
        with open(os.path.join(workflows_dir, "broken.yaml"), "w") as f:
            f.write(malformed_yaml)
        import json as _json
        state_json_str = (
            '{"version": 1, "workflow": "broken", "step": "analyze", "agent": "genius"}'
        )
        state_block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + state_json_str
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(
            "/workflow force-next-step\n\n" + state_block,
            prompt_base,
            workflows_dir,
            is_bot=False,
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("does not exist", result["REASON"])


class TestForceNextStepFallbackFromComments(unittest.TestCase):
    """Tests for force-next-step fallback: fetching state from issue comments."""

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(self._default_workflow_yaml())
        return prompt_base, workflows_dir

    @staticmethod
    def _default_workflow_yaml():
        return (
            "name: default\n"
            "description: Standard workflow\n"
            "trigger:\n"
            "  agents: [genius]\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: genius\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: ctx\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: genius\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    @staticmethod
    def _build_state_block(workflow="default", step="analyze"):
        import json as _json
        state = {
            "version": 1,
            "workflow": workflow,
            "step": step,
            "agent": "genius",
        }
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    @staticmethod
    def _comments_with_state(state_block):
        return [
            {"body": "Some discussion comment", "id": 111},
            {"body": state_block, "id": 222},
        ]

    def test_fallback_fetches_state_from_comments(self):
        """When trigger_body has no state but repo/issue_number provided, fetch from comments."""
        from unittest.mock import patch

        prompt_base, workflows_dir = self._setup_dirs()
        state_block = self._build_state_block(workflow="default", step="analyze")
        comments = self._comments_with_state(state_block)

        with patch("resolve_config.get_issue_comments", return_value=comments):
            result = resolve_config(
                "/workflow force-next-step",
                prompt_base,
                workflows_dir,
                is_bot=False,
                repo="owner/repo",
                issue_number=42,
            )

        self.assertEqual(result["SKIP_AGENT"], "false")
        # analyze.next = context -> agent ctx
        self.assertEqual(result["TASK"], "ctx")

    def test_fallback_no_state_in_comments_returns_error(self):
        """When comments have no workflow state, still returns error."""
        from unittest.mock import patch

        prompt_base, workflows_dir = self._setup_dirs()
        comments = [{"body": "No state here", "id": 111}]

        with patch("resolve_config.get_issue_comments", return_value=comments):
            result = resolve_config(
                "/workflow force-next-step",
                prompt_base,
                workflows_dir,
                is_bot=False,
                repo="owner/repo",
                issue_number=42,
            )

        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("no workflow state found", result["REASON"])

    def test_no_fallback_without_repo_or_issue_number(self):
        """Without repo/issue_number, no fallback occurs — returns error as before."""
        prompt_base, workflows_dir = self._setup_dirs()

        result = resolve_config(
            "/workflow force-next-step",
            prompt_base,
            workflows_dir,
            is_bot=False,
            repo=None,
            issue_number=None,
        )

        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("no workflow state found", result["REASON"])


class TestWorkflowDrivenResolution(unittest.TestCase):
    """Tests for workflow-driven resolution in resolve_config() agent_config branch."""

    @staticmethod
    def _default_workflow_yaml():
        return (
            "name: default\n"
            "description: Standard workflow\n"
            "trigger:\n"
            "  agents: [genius]\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: genius\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: ctx\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: genius\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    @staticmethod
    def _build_state_json(workflow="default", step="analyze", agent="genius"):
        import json as _json
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({"version": 1, "workflow": workflow, "step": step, "agent": agent})
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(self._default_workflow_yaml())
        return prompt_base, workflows_dir

    def test_workflow_driven_resolution_with_agent_hint(self):
        """Workflow-driven resolution resolves next step from workflow state."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "default", "step": "analyze", "agent": "genius",
                "dispatch": {"task_hint": "dev", "complexity": "medium"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # After "analyze", next step is "context" with agent "ctx"
        self.assertEqual(result["TASK"], "ctx")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_workflow_driven_resolution_with_branch_result(self):
        """Workflow-driven resolution passes branch_result from dispatch dict."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "default", "step": "analyze", "agent": "genius",
                "dispatch": {"task_hint": "dev", "branch": "approved"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # Linear resolution (no conditions in default workflow) — follows "next" field
        self.assertEqual(result["TASK"], "ctx")

    def test_terminal_step_returns_skip(self):
        """Terminal step with dispatch.advance and no task_hint → resolve_next_from_workflow returns None → No valid task."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        # "review" is terminal (next: null) — resolve_next_from_workflow returns None
        # dispatch.advance with no task_hint → task = "" → SKIP_AGENT: No valid task found
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "default", "step": "review", "agent": "review",
                "dispatch": {},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("No valid task", result["REASON"])

    def test_fallback_to_agent_hint_when_no_workflow_state(self):
        """When no workflow state exists, fall back to agent hint (task field)."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1,
                "dispatch": {"task_hint": "dev", "complexity": "high"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["COMPLEXITY"], "high")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_backward_compatibility_simple_agent_run_config(self):
        """Simple config without workflow state still works (now via dispatch in WORKFLOW_STATE)."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1,
                "dispatch": {
                    "task_hint": "dev", "complexity": "low",
                    "reasoning": "high", "temperature": 0.5,
                },
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["COMPLEXITY"], "low")
        self.assertEqual(result["LLM_REASONING"], "high")
        self.assertEqual(result["CONFIG_TEMPERATURE"], "0.5")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_workflow_driven_overrides_agent_hint(self):
        """Workflow-driven resolution overrides the agent hint with the actual next step agent."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        # Agent hint is "dev" but workflow says after "plan" comes "implement" -> agent "dev"
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "default", "step": "plan", "agent": "genius",
                "dispatch": {"task_hint": "dev", "complexity": "medium"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")

    def test_missing_workflow_file_returns_skip(self):
        """When workflow file doesn't exist, return SKIP_AGENT (broken state)."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "nonexistent", "step": "analyze", "agent": "genius",
                "dispatch": {"status": "advance"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # resolve_next_from_workflow returns None for missing workflow when agent_hint=None
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("REASON", result)

    def test_dispatch_advance_default_workflow_analyze_to_ctx(self):
        """Regression #377: dispatch.advance with no task field on default workflow resolves analyze→ctx.

        Reproduces the exact scenario from the issue: genius finishes the 'analyze' step
        with dispatch.advance (no explicit task). The resolver should parse the workflow
        state, find the next step in default.yaml ('context'), and select agent 'ctx'.
        """
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "default", "step": "analyze", "agent": "genius",
                "dispatch": {"status": "advance", "complexity": "medium"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "ctx")
        self.assertEqual(result["SKIP_AGENT"], "false")


class TestE2EDispatchWorkflowResolution(unittest.TestCase):
    """End-to-end: agent finishes with dispatch → config resolver uses workflow YAML."""

    @staticmethod
    def _approval_workflow_yaml():
        return (
            "name: approval\n"
            "description: Approval workflow with conditional branching\n"
            "trigger:\n"
            "  agents: [genius]\n"
            "steps:\n"
            "  - name: spec\n"
            "    agent: po\n"
            "    next: spec_approval\n"
            "  - name: spec_approval\n"
            "    agent: genius\n"
            "    conditions:\n"
            "      - on_result: approved\n"
            "        next: implement\n"
            "      - on_result: rejected\n"
            "        next: spec\n"
            "      - on_result: default\n"
            "        next: implement\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    @staticmethod
    def _build_state_json(workflow, step, agent, dispatch_status=None, dispatch_task=None):
        import json as _json
        state = {
            "version": 1,
            "workflow": workflow,
            "step": step,
            "agent": agent,
        }
        if dispatch_status:
            state["dispatch"] = {
                "status": dispatch_status,
                "task": dispatch_task or "",
                "complexity": "medium",
            }
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, "approval.yaml"), "w") as f:
            f.write(self._approval_workflow_yaml())
        return prompt_base, workflows_dir

    def test_dispatch_advance_resolves_correct_next_agent(self):
        """Agent finishes with dispatch→advance; resolver picks up next agent from workflow."""
        prompt_base, workflows_dir = self._setup_dirs()
        # Agent "po" finished step "spec", dispatch advances to next step
        state_json = self._build_state_json(
            workflow="approval", step="spec", agent="po",
            dispatch_status="advance", dispatch_task="",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # After "spec", next step is "spec_approval" with agent "genius"
        self.assertEqual(result["TASK"], "genius")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_dispatch_advance_without_task_field(self):
        """Regression: dispatch-only advance (no task field) must resolve next agent from workflow."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="approval", step="spec", agent="po",
            dispatch_status="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # After "spec", next step is "spec_approval" with agent "genius"
        self.assertEqual(result["TASK"], "genius")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_dispatch_with_branch_approved(self):
        """Dispatch with branch=approved resolves to implement step."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "approval", "step": "spec_approval", "agent": "genius",
                "dispatch": {"status": "advance", "task_hint": "dev", "branch": "approved"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # branch_result="approved" → condition routes to "implement" → agent "dev"
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_dispatch_with_branch_rejected(self):
        """Dispatch with branch=rejected loops back to spec step."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "approval", "step": "spec_approval", "agent": "genius",
                "dispatch": {"status": "advance", "task_hint": "dev", "branch": "rejected"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # branch_result="rejected" → condition routes to "spec" → agent "po"
        self.assertEqual(result["TASK"], "po")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_full_workflow_simulation_through_resolver(self):
        """Simulate a full workflow run through resolve_config with mock workflow state."""
        prompt_base, workflows_dir = self._setup_dirs()

        # Step 1: spec → spec_approval
        state = self._build_state_json(
            "approval", "spec", "po",
            dispatch_status="advance", dispatch_task="",
        )
        result = resolve_config(state, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "genius")
        self.assertEqual(result["ROLE_NAME"], "genius")

        # Step 2: spec_approval (approved) → implement
        import json as _json
        state = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "approval", "step": "spec_approval", "agent": "genius",
                "dispatch": {"status": "advance", "task_hint": "genius", "branch": "approved"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["ROLE_NAME"], "dev")

        # Step 3: implement → review
        state = self._build_state_json(
            "approval", "implement", "dev",
            dispatch_status="advance", dispatch_task="",
        )
        result = resolve_config(state, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "review")
        self.assertEqual(result["ROLE_NAME"], "review")

        # Step 4: review (terminal) → SKIP
        state = self._build_state_json(
            "approval", "review", "review",
            dispatch_status="advance", dispatch_task="",
        )
        result = resolve_config(state, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")


class TestDispatchAdvanceDefaultWorkflow(unittest.TestCase):
    """E2E: dispatch.advance with default workflow resolves next agent from workflow YAML.

    Regression test for issue #377 — ensures the exact body format from the issue
    (WORKFLOW_STATE + dispatch.advance without task field) correctly resolves
    to the ctx agent when the current step is 'analyze' in the 'default' workflow.
    """

    @staticmethod
    def _default_workflow_yaml():
        return (
            "name: default\n"
            "description: Standard workflow\n"
            "trigger:\n"
            "  agents: [genius]\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: genius\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: ctx\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: genius\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    @staticmethod
    def _build_state_json(workflow="default", step="analyze", agent="genius",
                          dispatch_status=None, dispatch_task=None):
        import json as _json
        state = {
            "version": 1,
            "workflow": workflow,
            "step": step,
            "agent": agent,
        }
        if dispatch_status:
            state["dispatch"] = {
                "status": dispatch_status,
                "task": dispatch_task or "",
                "complexity": "medium",
            }
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(self._default_workflow_yaml())
        return prompt_base, workflows_dir

    def test_dispatch_advance_analyze_resolves_to_ctx(self):
        """Exact scenario from issue #377: dispatch.advance from analyze → ctx."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="default", step="analyze", agent="genius",
            dispatch_status="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # After "analyze" in default workflow, next step is "context" → agent "ctx"
        self.assertEqual(result["TASK"], "ctx")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_dispatch_advance_context_resolves_to_genius(self):
        """dispatch.advance from context step → genius (plan step)."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="default", step="context", agent="ctx",
            dispatch_status="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "genius")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_dispatch_advance_implement_resolves_to_dev(self):
        """dispatch.advance from implement step → dev (implement step)."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="default", step="plan", agent="genius",
            dispatch_status="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_dispatch_advance_terminal_step_returns_skip(self):
        """dispatch.advance from terminal review step → SKIP_AGENT."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="default", step="review", agent="review",
            dispatch_status="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # Terminal step: resolve_next_from_workflow returns None,
        # dispatch.advance falls back to empty task_hint → no valid task
        self.assertEqual(result["SKIP_AGENT"], "true")


class TestForceNextStepWithWorkflowDriven(unittest.TestCase):
    """Force-next-step command alongside workflow-driven resolution."""

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(TestWorkflowDrivenResolution._default_workflow_yaml())
        return prompt_base, workflows_dir

    def _build_state_json(self, workflow="default", step="analyze", agent="genius"):
        import json as _json
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({"version": 1, "workflow": workflow, "step": step, "agent": agent})
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def test_force_next_step_with_workflow_driven_no_conflict(self):
        """force-next-step works correctly when there's no agentRunConfig (human command)."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json("default", "analyze", "genius")
        body = "/workflow force-next-step\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # Auto-advance: analyze.next = context → agent ctx
        self.assertEqual(result["TASK"], "ctx")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_force_next_step_explicit_target_with_workflow_driven(self):
        """force-next-step with explicit target overrides normal workflow progression."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json("default", "analyze", "genius")
        body = "/workflow force-next-step implement\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # Explicit target "implement" → agent dev (bypasses normal analyze→context flow)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["SKIP_AGENT"], "false")


class TestHumanWorkflowOverridePrecedence(unittest.TestCase):
    """Human /workflow command takes precedence over workflow state."""

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(TestWorkflowDrivenResolution._default_workflow_yaml())
        with open(os.path.join(workflows_dir, "review-only.yaml"), "w") as f:
            f.write(
                "name: review-only\n"
                "description: Review only workflow\n"
                "trigger:\n"
                "  agents: [review]\n"
                "steps:\n"
                "  - name: review\n"
                "    agent: review\n"
                "    next: null\n"
            )
        return prompt_base, workflows_dir

    def test_workflow_command_precedence_over_workflow_state(self):
        """/workflow command takes precedence even when workflow state exists."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + '{"version": 1, "workflow": "default", "step": "analyze", "agent": "genius"}'
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        body = "/workflow review-only\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # /workflow review-only should take precedence and resolve to first agent of review-only
        self.assertEqual(result["ROLE_NAME"], "review")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_workflow_command_precedence_over_mention(self):
        """/workflow command takes precedence over @@mention."""
        prompt_base, workflows_dir = self._setup_dirs()
        body = "/workflow default\n@@dev please implement something"
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # /workflow default triggers the workflow — genius is the trigger agent
        self.assertEqual(result["ROLE_NAME"], "genius")
        self.assertEqual(result["SKIP_AGENT"], "false")


class TestResolveConfigWithRealWorkflowFile(unittest.TestCase):
    """Integration tests using the real workflows/default.yaml file from the repo root.

    Regression for issue #377 — ensures the full resolve_config pipeline correctly
    parses WORKFLOW_STATE markers, dispatch.advance without task field, and resolves
    the next agent against the actual workflow definition on disk.
    """

    @staticmethod
    def _real_workflows_dir():
        """Return path to the real workflows/ directory in the repo root."""
        from pathlib import Path
        return Path(__file__).resolve().parents[2] / "workflows"

    @staticmethod
    def _setup_prompt_base():
        """Create minimal prompt files required by resolve_config."""
        prompt_base = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        return prompt_base

    @staticmethod
    def _build_issue_body():
        """Reproduce the body from issue #377 using dispatch in workflow state.

        Contains:
        - WORKFLOW_STATE block: workflow=default, step=analyze, agent=genius,
          with dispatch.advance (no task field)
        Expected: resolves to agent 'ctx'.
        """
        return (
            "<summary>🤖 Workflow State (v1)</summary>\n"
            "\n"
            "```json\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"workflow": "default", "step": "analyze", "agent": "genius", "phase": "analysis", '
            '"dispatch": {"status": "advance"}, '
            '"history": [{"step": "analyze", "agent": "genius", "summary": "delegate context collection"}], '
            '"metrics": {"run_id": "d17d26d4-c4de-4808-b80b-e353d79e0563", "model_name": "default", '
            '"agent_role": "genius", "input_tokens": 8069, "output_tokens": 276, '
            '"tool_call_latency_ms": 9842.795133590698, "agent_runtime_ms": 9842.8}, '
            '"issue": {"number": 377, "url": "https://github.com/olivier-norture/git-flow-ai/issues/377", '
            '"title": "fix workflow force-next-step requires an active workflow", '
            '"author": "olivier-norture", "repo": "olivier-norture/git-flow-ai"}}\n'
            "<!-- WORKFLOW_STATE_END -->\n"
            "```\n"
            "\n"
            "</details>\n"
            "<!-- bot-agent-genius -->\n"
            "*Genius answer*\n"
            "\n"
            "I need to understand the code flow for `force-next-step`.\n"
        )

    def test_exact_issue_input_resolves_to_ctx(self):
        """Exact scenario from issue #377: dispatch.advance from analyze → ctx agent.

        The body contains a WORKFLOW_STATE (workflow=default, step=analyze, agent=genius)
        and an agentRunConfig with dispatch.advance but NO task field.
        resolve_config should parse the state, look up the next step in the real
        workflows/default.yaml, and resolve to agent 'ctx'.
        """
        prompt_base = self._setup_prompt_base()
        workflows_dir = str(self._real_workflows_dir())
        body = self._build_issue_body()

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=True)

        self.assertEqual(result["TASK"], "ctx",
            "dispatch.advance from 'analyze' step in default workflow should resolve to 'ctx' agent")
        self.assertEqual(result["SKIP_AGENT"], "false")
