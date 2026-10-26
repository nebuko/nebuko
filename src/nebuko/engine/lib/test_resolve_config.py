"""Tests for resolve_config.py."""

import json
import tempfile
import os
from pathlib import Path

import pytest

from nebuko.engine.lib.resolve_config import (
    parse_workflow_command,
    is_bot_comment,
    extract_mention_agent,
    normalize_resume_input,
    BARE_RESUME_INPUT,
    resolve_config,
    resolve_next_agent_from_workflow,
    count_consecutive_same_agent,
)
from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT


import unittest


def _history_entry(step: str, agent: str, summary: str = "") -> dict:
    """A raw history entry carrying the mandatory factual outcome (issue #964)."""
    return {
        "step": step,
        "agent": agent,
        "summary": summary,
        "turn_outcome": {"status": "completed", "appreciation": "approved"},
    }


def _completed_outcome(**overrides) -> dict:
    """A raw ``turn_outcome`` payload for a completed turn."""
    return {"status": "completed", "appreciation": "approved", **overrides}


def _hold_outcome(**overrides) -> dict:
    """A raw ``turn_outcome`` payload for a parked (resumable) turn."""
    return {
        "status": "completed",
        "appreciation": "hold",
        "hold_kind": "awaiting_input",
        **overrides,
    }


def _real_workflows_dir() -> str:
    """The shipped workflow dir, not the per-machine ``.nebuko`` override."""
    from nebuko.engine.lib.default_paths import checkout_root

    return str(checkout_root() / "defaults" / "workflows")


class TestIsBotComment:
    """Test bot comment detection."""

    def test_bot_marker_at_start(self):
        body = "<!-- bot-agent-dev -->\n@@architect please analyze"
        assert is_bot_comment(body) is True

    def test_bot_marker_in_first_five_lines(self):
        lines = ["line 1", "line 2", "line 3", "<!-- bot-agent-context -->", "content"]
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
        result = extract_mention_agent("@@architect analyze then @@dev implement")
        assert result == "dev"

    def test_no_mention(self):
        result = extract_mention_agent("No mentions here")
        assert result is None

    def test_all_agent_types(self):
        for agent in ("architect", "context", "dev", "review", "po"):
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
        # An agent-to-agent hand-off is an imperative ``function_call``
        # (issue #1033): the verdict no longer names the target.
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 6, "turn_outcome": {"status": "completed", '
            '"appreciation": "approved"}, '
            '"function_call": {"function": "call_agent", "agent_name": "dev"}, '
            '"prefs": {"complexity": "high"}}\n'
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
        body = "@@architect please create a plan"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "architect"
        assert result["ROLE_NAME"] == "architect"
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
        # Every non-routing knob rides on ``prefs`` (issue #964).
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 6, "turn_outcome": {"status": "completed", '
            '"appreciation": "approved"}, '
            '"function_call": {"function": "call_agent", "agent_name": "dev"}, '
            '"prefs": {"complexity": "low", "temperature": 0.5, '
            '"model": "custom-model", "top_p": 0.9, "top_k": 50, '
            '"repetition_penalty": 1.2}}\n'
            "<!-- WORKFLOW_STATE_END -->"
        )

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["CONFIG_MODEL"] == "custom-model"
        assert result["CONFIG_TEMPERATURE"] == "0.5"
        assert result["CONFIG_TOP_P"] == "0.9"
        assert result["CONFIG_TOP_K"] == "50"
        assert result["CONFIG_REPETITION_PENALTY"] == "1.2"
        # ``reasoning`` was a Dispatch knob with no RunPrefs counterpart: the
        # reasoning mode now comes from the agent's complexity tier.
        assert result["LLM_REASONING"] == "none"

    def test_context_complexity_handling(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = "@@context gather context about the codebase"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["COMPLEXITY"] == "medium"

    def test_workflow_routing_integration(self):
        """When workflow is specified, resolve_workflow handles routing."""
        prompt_base, workflows_dir = self._setup_dirs()
        # /nebuko without a valid workflow file and no @@mention → SKIP_AGENT
        body = "/nebuko run workflow --name nonexistent\nPlease analyze this issue."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # Falls through to @@mention (none found) → skips
        assert result["SKIP_AGENT"] == "true"


class TestCIBotMentionRouting:
    """Regression: CI-posted plain mention triggers keep routing to the agent.

    Sub-issue bootstrap ("@@architect") notifications now carry the universal
    bot marker (issue #474) and classify as bot. They must still resolve to the
    mentioned agent, not SKIP_AGENT.
    """

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        return prompt_base, workflows_dir

    def test_bootstrap_architect_with_universal_marker_routes_to_architect(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = "<!-- bot-workflow-terminal -->\n@@architect"

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "architect"
        assert result["ROLE_NAME"] == "architect"

    def test_plain_mention_with_extra_marker_routes_to_architect(self):
        prompt_base, workflows_dir = self._setup_dirs()
        body = "@@architect <!-- bot-workflow-terminal --> <!-- bot-trigger: architect -->"

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "architect"

    def test_legacy_bot_mention_with_prose_still_skips(self):
        # Legacy bot comment (bot-agent- marker) with prose after the
        # mention must keep old behavior: SKIP_AGENT.
        prompt_base, workflows_dir = self._setup_dirs()
        body = "<!-- bot-agent-dev -->\n@@architect please analyze this"

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["SKIP_AGENT"] == "true"

    def test_bot_answer_with_mention_and_no_state_still_skips(self):
        # Agent answer comment without WORKFLOW_STATE whose text mentions
        # another agent must NOT trigger that agent.
        prompt_base, workflows_dir = self._setup_dirs()
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- bot-agent-architect -->\n"
            "*Architect answer*\n\nHanding off to @@dev for implementation."
        )

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["SKIP_AGENT"] == "true"

    def test_legacy_bot_plain_mention_without_universal_marker_still_skips(self):
        # Legacy bot comment (bot-agent- marker only, no universal marker)
        # with a plain mention must keep its historical routing: SKIP_AGENT.
        # The ci_mention gate requires the universal marker, so legacy
        # histories are unaffected.
        prompt_base, workflows_dir = self._setup_dirs()
        body = "<!-- bot-agent-dev -->\n@@architect"

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["SKIP_AGENT"] == "true"

    def test_in_body_state_is_not_plain_mention(self):
        # A body embedding a full WORKFLOW_STATE is not a plain mention
        # trigger (the state JSON counts as non-mention content), so it is
        # resolved via the state path instead of the ci_mention shortcut.
        # Real CI triggers (e.g. the sub-issue bootstrap) never embed a state
        # in the trigger body — they arrive as mention-only comments.
        prompt_base, workflows_dir = self._setup_dirs()
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(
                "name: default\n"
                "description: Standard workflow\n"
                "steps:\n"
                "  - name: analyze\n"
                "    agent: architect\n"
                "    next: context\n"
                "  - name: context\n"
                "    agent: context\n"
                "    next: null\n"
            )
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "@@architect\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 6, "workflow": "default", "step": "analyze", '
            '"turn_outcome": {"status": "completed", "appreciation": "approved"}, '
            '"prefs": {"complexity": "medium"}}\n'
            "<!-- WORKFLOW_STATE_END -->"
        )

        result = resolve_config(body, prompt_base, workflows_dir)

        # The workflow edge wins over the mention: analyze -> context.
        assert result["ROLE_NAME"] == "context"



class TestResolveConfigWithWorkflowCommand:
    """Test resolve_config with /nebuko command."""

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
                "steps:\n"
                "  - name: analyze\n"
                "    agent: architect\n"
                "    next: null\n"
            )
        with open(os.path.join(workflows_dir, "review-only.yaml"), "w") as f:
            f.write(
                "name: review-only\n"
                "description: Review only\n"
                "steps:\n"
                "  - name: review\n"
                "    agent: review\n"
                "    next: null\n"
            )
        with open(os.path.join(workflows_dir, "multi.yaml"), "w") as f:
            f.write(
                "name: multi\n"
                "description: Multi-step\n"
                "steps:\n"
                "  - name: analyze\n"
                "    agent: architect\n"
                "    next: implement\n"
                "  - name: implement\n"
                "    agent: dev\n"
                "    next: null\n"
            )
        return prompt_base, workflows_dir

    def test_workflow_default_resolves_architect(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name default\nPlease analyze this issue."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "architect"
        assert result["ROLE_NAME"] == "architect"
        assert result["WORKFLOW_NAME"] == "default"
        assert result["SKIP_AGENT"] == "false"

    def test_workflow_review_only_resolves_review(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name review-only\nReview my PR please."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "review"
        assert result["ROLE_NAME"] == "review"
        assert result["WORKFLOW_NAME"] == "review-only"

    def test_run_workflow_step_enters_at_named_step(self):
        """Issue #789: --step seeds the run at the requested step."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name multi --step implement\nGo."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "dev"
        assert result["ROLE_NAME"] == "dev"
        assert result["WORKFLOW_NAME"] == "multi"
        assert result["WORKFLOW_STEP"] == "implement"
        assert result["SKIP_AGENT"] == "false"

    def test_run_workflow_without_step_is_backward_compatible(self):
        """Omitting --step still enters at the first step (issue #789)."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name multi\nGo."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "architect"
        assert result["WORKFLOW_NAME"] == "multi"
        assert result["WORKFLOW_STEP"] == "analyze"

    def test_run_workflow_unknown_step_fails_loudly(self):
        """An unknown --step target skips with a diagnostic (issue #789)."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name multi --step nope\nGo."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["SKIP_AGENT"] == "true"
        assert result.get("TASK", "") == ""
        assert "not found in workflow 'multi'" in result["REASON"]

    def test_run_workflow_invalid_step_name_fails_loudly(self):
        """Path-traversal in --step is rejected like workflow names (issue #789)."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name multi --step ../etc/passwd\nGo."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["SKIP_AGENT"] == "true"
        assert "invalid step name" in result["REASON"]

    def test_workflow_nonexistent_falls_through_mentions(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name nonexistent\n@@architect help me"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # Nonexistent workflow -> falls through to @@mention
        assert result["TASK"] == "architect"
        assert result["ROLE_NAME"] == "architect"
        assert result["WORKFLOW_NAME"] == ""
        assert result["SKIP_AGENT"] == "false"

    def test_workflow_malformed_name_falls_through_mentions(self):
        """Workflow names with special chars are rejected and fall through."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name ../etc/passwd\n@@dev help"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == ""
        assert result["SKIP_AGENT"] == "false"

    def test_workflow_empty_steps_falls_through_mentions(self):
        """A step-less workflow has no entry agent and falls through to @@mention."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        with open(os.path.join(workflows_dir, "no-steps.yaml"), "w") as f:
            f.write(
                "name: no-steps\n"
                "description: Workflow with no steps\n"
                "steps: []\n"
            )
        body = "/nebuko run workflow --name no-steps\n@@context gather context"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "context"
        assert result["ROLE_NAME"] == "context"
        assert result["WORKFLOW_NAME"] == ""
        assert result["SKIP_AGENT"] == "false"

    def test_workflow_not_on_first_line_ignored(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "Some intro text\n/nebuko run workflow --name default"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # Not on first line, no @@mention -> skip
        assert result["SKIP_AGENT"] == "true"

    def test_workflow_not_on_first_line_falls_through_mention(self):
        """When /nebuko not on first line but @@mention present, use mention."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "Some intro text\n/nebuko run workflow --name default\n@@dev please help"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["TASK"] == "dev"
        assert result["ROLE_NAME"] == "dev"
        assert result["WORKFLOW_NAME"] == ""

    def test_workflow_command_takes_precedence_over_mention(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name default\n@@review please also review"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        # /nebuko takes priority over @@mention
        assert result["TASK"] == "architect"
        assert result["WORKFLOW_NAME"] == "default"

    def test_workflow_bot_comment_skips(self):
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "<!-- bot-agent-dev -->\n/nebuko run workflow --name default"

        result = resolve_config(body, prompt_base, workflows_dir)

        # Bot comments should skip even with /nebuko
        assert result["SKIP_AGENT"] == "true"

    def test_step_skills_passed_through_from_workflow(self):
        """STEP_SKILLS from workflow resolver are passed through in resolve_config output."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        # Create a workflow whose routed-to step carries skills
        with open(os.path.join(workflows_dir, "with-skills.yaml"), "w") as f:
            f.write(
                "name: with-skills\n"
                "description: Workflow with skills\n"
                "steps:\n"
                "  - name: plan\n"
                "    agent: architect\n"
                "    next: implement\n"
                "  - name: implement\n"
                "    agent: dev\n"
                "    skill: [python, rust]\n"
                "    next: null\n"
            )
        # The state's turn outcome routes plan -> implement (the skilled step)
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 6, "workflow": "with-skills", "step": "plan", '
            '"turn_outcome": {"status": "completed", "appreciation": "approved"}}\n'
            "<!-- WORKFLOW_STATE_END -->"
        )

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["ROLE_NAME"] == "dev"
        assert result["WORKFLOW_NAME"] == "with-skills"
        assert result["STEP_SKILLS"] == "python,rust"

    def test_step_skill_modes_are_carried_alongside_the_name_string(self):
        """A step can split its skills into inject and discover (issue #955)."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        with open(os.path.join(workflows_dir, "with-modes.yaml"), "w") as f:
            f.write(
                "name: with-modes\n"
                "description: Workflow with skill modes\n"
                "steps:\n"
                "  - name: plan\n"
                "    agent: architect\n"
                "    next: implement\n"
                "  - name: implement\n"
                "    agent: dev\n"
                "    skill:\n"
                "      - inject: [python]\n"
                "      - discover: [rust]\n"
                "    next: null\n"
            )
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 6, "workflow": "with-modes", "step": "plan", '
            '"turn_outcome": {"status": "completed", "appreciation": "approved"}}\n'
            "<!-- WORKFLOW_STATE_END -->"
        )

        result = resolve_config(body, prompt_base, workflows_dir)

        assert result["STEP_SKILLS"] == "python,rust"
        assert result["STEP_SKILL_SPECS"] == (
            '[{"mode":"inject","names":["python"]},'
            '{"mode":"discover","names":["rust"]}]'
        )

    def test_step_skills_empty_when_not_defined(self):
        """STEP_SKILLS is empty when workflow step has no skill field."""
        prompt_base, workflows_dir = self._setup_dirs_with_workflows()
        body = "/nebuko run workflow --name default\nPlease analyze this issue."

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        assert result["STEP_SKILLS"] == ""



class TestResolveNextAgentFromWorkflow(unittest.TestCase):
    """Tests for resolve_next_agent_from_workflow function."""

    @staticmethod
    def _real_workflows_dir():
        """The shipped workflow dir, not the per-machine ``.nebuko`` override."""
        return _real_workflows_dir()

    def test_task_hint_overrides_workflow(self):
        result = resolve_next_agent_from_workflow("dev", "default", "analyze", self._real_workflows_dir())
        self.assertEqual(result, "dev")

    def test_resolves_next_from_default_workflow(self):
        result = resolve_next_agent_from_workflow(None, "default", "analyze", self._real_workflows_dir())
        self.assertEqual(result, "context")

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
        from nebuko.engine.lib.workflow_state import HistoryEntry
        count = count_consecutive_same_agent([], "dev")
        self.assertEqual(count, 0)

    def test_single_match(self):
        from nebuko.engine.lib.workflow_state import HistoryEntry
        history = [HistoryEntry(step="s", agent="dev", turn_outcome=_completed_outcome())]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 1)

    def test_mixed_agents(self):
        from nebuko.engine.lib.workflow_state import HistoryEntry
        history = [
            HistoryEntry(step="s1", agent="architect", turn_outcome=_completed_outcome()),
            HistoryEntry(step="s2", agent="dev", turn_outcome=_completed_outcome()),
            HistoryEntry(step="s3", agent="dev", turn_outcome=_completed_outcome()),
        ]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 2)

    def test_different_agent(self):
        from nebuko.engine.lib.workflow_state import HistoryEntry
        history = [HistoryEntry(step="s1", agent="architect", turn_outcome=_completed_outcome())]
        count = count_consecutive_same_agent(history, "dev")
        self.assertEqual(count, 0)



class TestForceNextStepIntegration(unittest.TestCase):
    """Integration tests for /nebuko force-next-step command."""

    @staticmethod
    def _default_workflow_yaml():
        return (
            "name: default\n"
            "description: Standard workflow\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: context\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: architect\n"
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
        result = self._resolve_with_state("/nebuko force-step step --name review", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "review")

    def test_force_next_step_with_explicit_target(self):
        state_json = self._build_state_json(workflow="default", step="analyze")
        result = self._resolve_with_state("/nebuko force-step step --name implement", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "dev")

    def test_force_next_step_no_workflow_state(self):
        prompt_base, workflows_dir = self._setup_dirs()
        result = resolve_config(
            "/nebuko force-step step --name context", prompt_base, workflows_dir, is_bot=False
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("no workflow state found", result["REASON"])

    def test_force_next_step_invalid_target_step(self):
        state_json = self._build_state_json(workflow="default", step="analyze")
        result = self._resolve_with_state("/nebuko force-step step --name nonexistent", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("not found", result["REASON"])

    def test_force_next_step_path_traversal_workflow_name(self):
        state_json = self._build_state_json(workflow="../etc/passwd", step="hack")
        result = self._resolve_with_state("/nebuko force-step step --name context", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("invalid workflow name", result["REASON"])

    def test_force_next_step_path_traversal_target_step(self):
        state_json = self._build_state_json(workflow="default", step="analyze")
        result = self._resolve_with_state("/nebuko force-step step --name ../../etc/passwd", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("invalid target step", result["REASON"])

    def test_force_next_step_invalid_workflow_file(self):
        state_json = self._build_state_json(workflow="nonexistent_workflow", step="analyze")
        result = self._resolve_with_state("/nebuko force-step step --name context", state_json)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("not found", result["REASON"])

    def test_force_step_explicit_target_ignores_current_step(self):
        # The new grammar requires an explicit --name, so an unknown current
        # step no longer matters (issue #635 removed the auto-advance arm).
        state_json = self._build_state_json(workflow="default", step="nonexistent_step")
        result = self._resolve_with_state("/nebuko force-step step --name context", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "context")

    def test_force_next_step_preserves_parent_workflow(self):
        # Note: parent workflow fields are preserved by resolve_config.
        # Downstream resolve_workflow.py may reject if parent workflow YAML
        # doesn't exist — that's separate from force-next-step logic.
        state_json = self._build_state_json(workflow="default", step="implement")
        result = self._resolve_with_state("/nebuko force-step step --name review", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["WORKFLOW_NAME"], "default")

    def test_force_next_step_jumps_to_context(self):
        state_json = self._build_state_json(workflow="default", step="analyze")
        result = self._resolve_with_state("/nebuko force-step step --name context", state_json)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "context")

    def test_force_step_explicit_target_with_malformed_current_next(self):
        """Explicit --name resolves regardless of a dangling next on the current step
        (the auto-advance error path is gone under the new grammar, issue #635)."""
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        # Malformed workflow: analyze.next points to 'nonexistent' which doesn't exist
        malformed_yaml = (
            "name: broken\n"
            "description: Broken workflow\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: nonexistent\n"
            "  - name: context\n"
            "    agent: context\n"
            "    next: null\n"
        )
        with open(os.path.join(workflows_dir, "broken.yaml"), "w") as f:
            f.write(malformed_yaml)
        import json as _json
        state_json_str = (
            '{"version": 1, "workflow": "broken", "step": "analyze", "agent": "architect"}'
        )
        state_block = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + state_json_str
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(
            "/nebuko force-step step --name context\n\n" + state_block,
            prompt_base,
            workflows_dir,
            is_bot=False,
        )
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "context")


class TestDeleteCommentsCommand(unittest.TestCase):
    """Human `/nebuko delete comments` arm (issue #644).

    The command is human-only and emits the ``DELETE_COMMENTS`` env contract
    with no agent selected. ``--issue`` is optional on the issue surface and
    defaults to the invoking issue number.
    """

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        return prompt_base, workflows_dir

    def _resolve(self, body, issue_number=None):
        prompt_base, workflows_dir = self._setup_dirs()
        return resolve_config(
            body, prompt_base, workflows_dir, is_bot=False,
            issue_number=issue_number,
        )

    def test_explicit_issue_emits_contract(self):
        result = self._resolve("/nebuko delete comments --issue 123")
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "123")
        self.assertNotIn("DELETE_COMMENTS_FROM", result)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["AGENT_SELECTED"], "false")
        self.assertEqual(result["TASK"], "Delete comments on issue 123")

    def test_issue_defaults_to_invoking_issue(self):
        result = self._resolve("/nebuko delete comments", issue_number=456)
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "456")

    def test_explicit_issue_wins_over_invoking_issue(self):
        result = self._resolve(
            "/nebuko delete comments --issue 789", issue_number=456
        )
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "789")

    def test_from_scopes_the_deletion(self):
        # `--from` was restored in issue #867: delete that comment and every
        # later one on the target issue (supersedes the #674 removal).
        result = self._resolve(
            "/nebuko delete comments --issue 123 --from 5811583860"
        )
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "123")
        self.assertEqual(result["DELETE_COMMENTS_FROM"], "5811583860")
        self.assertEqual(result["SKIP_AGENT"], "true")

    def test_non_numeric_issue_is_a_command_error(self):
        result = self._resolve("/nebuko delete comments --issue abc", issue_number=456)
        self.assertNotIn("DELETE_COMMENTS", result)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["COMMAND_ERROR"], "true")
        self.assertIn("/nebuko delete comments", result["REASON"])

    def test_no_issue_and_no_invoking_issue_treated_as_absent(self):
        # Neither --issue nor an invoking issue number: command is absent.
        result = self._resolve("/nebuko delete comments")
        self.assertNotIn("DELETE_COMMENTS", result)

    def test_non_numeric_from_is_a_command_error(self):
        result = self._resolve("/nebuko delete comments --from abc", issue_number=456)
        self.assertNotIn("DELETE_COMMENTS", result)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["COMMAND_ERROR"], "true")
        self.assertIn("/nebuko delete comments", result["REASON"])

    def test_unicode_digit_issue_is_a_command_error(self):
        # '²' passes str.isdigit() but int() rejects it; the command is
        # malformed and answered, never executed or crashed (issues #644/#867).
        result = self._resolve("/nebuko delete comments --issue ²", issue_number=456)
        self.assertNotIn("DELETE_COMMENTS", result)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["COMMAND_ERROR"], "true")

    def test_scoped_delete_beats_active_turn_outcome(self):
        # A valid scoped delete must short-circuit before the cascade, exactly
        # as the unscoped one does (issue #867).
        body = (
            "/nebuko delete comments --issue 123 --from 5811583860\n\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"version": 1, "turn_outcome": {"status": "completed", '
            '"appreciation": "approved"}, "prefs": {"complexity": "high"}}\n'
            "<!-- WORKFLOW_STATE_END -->"
        )
        result = self._resolve(body, issue_number=867)
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "123")
        self.assertEqual(result["DELETE_COMMENTS_FROM"], "5811583860")
        self.assertEqual(result["AGENT_SELECTED"], "false")

    def test_bot_trigger_does_not_route_to_delete(self):
        result = self._resolve(
            "<!-- bot-workflow-terminal -->\n/nebuko delete comments --issue 123"
        )
        self.assertNotIn("DELETE_COMMENTS", result)


class TestCleanCommentsCommand(unittest.TestCase):
    """`/nebuko clean comments` — the agent-free alias (issue #679).

    The real-world bug (issue #590) is that ``/nebuko clean comments`` used
    to fall through the human-command cascade and boot context/architect. It is now a
    recognized local command that resolves to the same deterministic
    comment-removal contract as ``/nebuko delete comments`` and starts no
    agent, even when an active workflow turn outcome is present.
    """

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        return prompt_base, workflows_dir

    def _resolve(self, body, issue_number=None):
        prompt_base, workflows_dir = self._setup_dirs()
        return resolve_config(
            body, prompt_base, workflows_dir, is_bot=False,
            issue_number=issue_number,
        )

    @staticmethod
    def _state_block(outcome):
        state = {"version": 1, "turn_outcome": outcome}
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def test_clean_defaults_to_invoking_issue(self):
        result = self._resolve("/nebuko clean comments", issue_number=590)
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "590")
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["AGENT_SELECTED"], "false")
        self.assertEqual(result["ROLE_NAME"], "script")

    def test_clean_ignores_active_turn_outcome(self):
        # Regression #590: an active turn outcome must NOT absorb the command
        # and boot an agent; the local command still short-circuits.
        body = "/nebuko clean comments\n\n" + self._state_block(
            {"status": "completed", "appreciation": "approved"}
        )
        result = self._resolve(body, issue_number=590)
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["AGENT_SELECTED"], "false")
        self.assertEqual(result["TASK"], "Delete comments on issue 590")

    def test_clean_explicit_issue_targets_that_issue(self):
        # Review fix (issue #679): `--issue` must be forwarded, exactly as for
        # the `delete` alias, instead of silently defaulting to the invoking
        # issue.
        result = self._resolve("/nebuko clean comments --issue 123")
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "123")

    def test_clean_explicit_issue_wins_over_invoking_issue(self):
        result = self._resolve(
            "/nebuko clean comments --issue 789", issue_number=456
        )
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "789")

    def test_clean_from_scopes_the_deletion(self):
        # The literal #822 repro: `--from` is now valid and scoped (issue #867).
        result = self._resolve(
            "/nebuko clean comments --from 6095548211", issue_number=822
        )
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["DELETE_COMMENTS_ISSUE"], "822")
        self.assertEqual(result["DELETE_COMMENTS_FROM"], "6095548211")
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["AGENT_SELECTED"], "false")

    def test_clean_from_beats_active_review_turn_outcome(self):
        # Regression #822: an active review turn outcome must not absorb the
        # scoped clean command and boot the review agent.
        body = "/nebuko clean comments --from 6095548211\n\n" + self._state_block(
            {"status": "completed", "appreciation": "approved"}
        )
        result = self._resolve(body, issue_number=822)
        self.assertEqual(result["DELETE_COMMENTS"], "true")
        self.assertEqual(result["DELETE_COMMENTS_FROM"], "6095548211")
        self.assertEqual(result["AGENT_SELECTED"], "false")

    def test_malformed_clean_is_a_command_error_not_a_delete(self):
        # A malformed `clean` invocation must never delete comments (issue
        # #679); it is a command attempt, so the resolver answers it with the
        # usage hint and no agent boots (issue #867).
        for body in (
            "/nebuko clean",
            "/nebuko clean files",
            "/nebuko clean comments --unknown x",
            "/nebuko clean comments --issue abc",
        ):
            with self.subTest(body=body):
                result = self._resolve(body, issue_number=590)
                self.assertNotIn("DELETE_COMMENTS", result)
                self.assertEqual(result["SKIP_AGENT"], "true")
                self.assertEqual(result["COMMAND_ERROR"], "true")
                self.assertTrue(result["REASON"])

    def test_bot_clean_is_ignored(self):
        result = self._resolve(
            "<!-- bot-workflow-terminal -->\n/nebuko clean comments",
            issue_number=590,
        )
        self.assertNotIn("DELETE_COMMENTS", result)

    def test_bare_clean_is_not_a_command(self):
        result = self._resolve("clean comments", issue_number=590)
        self.assertNotIn("DELETE_COMMENTS", result)

    def test_prose_mentioning_clean_is_not_a_command(self):
        result = self._resolve("we should clean comments later", issue_number=590)
        self.assertNotIn("DELETE_COMMENTS", result)

    def test_clean_not_on_first_line_is_not_a_command(self):
        result = self._resolve("hello\n/nebuko clean comments", issue_number=590)
        self.assertNotIn("DELETE_COMMENTS", result)


class TestCommandAttemptRouting(unittest.TestCase):
    """A malformed /nebuko command is answered, never absorbed by the cascade.

    Regression for issue #822 (fixed by #867): ``/nebuko clean comments
    --from <id>`` was malformed, fell through the command short-circuits and was
    swallowed by an active workflow turn outcome, which booted the review agent.
    Any malformed ``/nebuko`` body now terminates with the ADR-0004 error
    contract and dispatches no agent.
    """

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        return prompt_base, workflows_dir

    def _resolve(self, body, issue_number=None, is_bot=False):
        prompt_base, workflows_dir = self._setup_dirs()
        return resolve_config(
            body,
            prompt_base,
            workflows_dir,
            is_bot=is_bot,
            issue_number=issue_number,
        )

    @staticmethod
    def _active_review_state():
        state = {
            "version": 1,
            "turn_outcome": {"status": "completed", "appreciation": "approved"},
        }
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def _body(self, command):
        return command + "\n\n" + self._active_review_state()

    def _assert_command_error(self, body):
        result = self._resolve(body, issue_number=867)
        self.assertNotIn("DELETE_COMMENTS", result, body)
        self.assertEqual(result.get("SKIP_AGENT"), "true", body)
        self.assertEqual(result.get("COMMAND_ERROR"), "true", body)
        self.assertTrue(result.get("REASON"), body)
        return result

    def test_non_numeric_from_is_a_command_error(self):
        result = self._assert_command_error(
            self._body("/nebuko clean comments --from abc")
        )
        self.assertIn("/nebuko clean comments", result["REASON"])

    def test_unknown_flag_is_a_command_error(self):
        result = self._assert_command_error(
            self._body("/nebuko clean comments --bogus x")
        )
        self.assertIn("/nebuko clean comments", result["REASON"])

    def test_unknown_action_uses_aggregate_usage(self):
        result = self._assert_command_error(self._body("/nebuko frobnicate x"))
        self.assertIn("unrecognized command", result["REASON"])

    def test_approve_bogus_flag_is_a_command_error(self):
        result = self._assert_command_error(self._body("/nebuko approve --bogus"))
        self.assertIn("/nebuko approve", result["REASON"])

    def test_command_error_dispatches_no_agent(self):
        result = self._assert_command_error(
            self._body("/nebuko clean comments --from abc")
        )
        self.assertNotEqual(result.get("AGENT_SELECTED"), "true")
        self.assertNotIn("TASK", result)

    def test_bot_command_attempt_is_ignored(self):
        body = "/nebuko clean comments --from abc\n<!-- bot-workflow-terminal -->"
        result = self._resolve(body, issue_number=867, is_bot=True)
        self.assertNotIn("COMMAND_ERROR", result)
        self.assertNotIn("DELETE_COMMENTS", result)

    def test_multiline_feedback_is_not_a_command_error(self):
        body = self._body(
            "/nebuko approve gate --feedback\n"
            "1. Flat\n"
            "2. Remove this section\n"
            "3. cache_hit_token is fine"
        )
        result = self._resolve(body, issue_number=919)
        self.assertNotIn("COMMAND_ERROR", result)


class TestUpdateCircuitBreakerCommand(unittest.TestCase):
    """Human `/nebuko update circuit-breaker --value <n>` arm (issue #839).

    The command is human-only and agent-free: it emits the
    ``UPDATE_CIRCUIT_BREAKER`` env contract (no agent selected, no call budget
    consumed). ``circuit_breaker`` is accepted as an alias spelling of the
    canonical ``circuit-breaker`` resource.
    """

    def _setup_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        return prompt_base, workflows_dir

    def _resolve(self, body, issue_number=None):
        prompt_base, workflows_dir = self._setup_dirs()
        return resolve_config(
            body, prompt_base, workflows_dir, is_bot=False,
            issue_number=issue_number,
        )

    def test_valid_command_emits_contract(self):
        result = self._resolve("/nebuko update circuit-breaker --value 35")
        self.assertEqual(result["UPDATE_CIRCUIT_BREAKER"], "true")
        self.assertEqual(result["UPDATE_CIRCUIT_BREAKER_VALUE"], "35")
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["AGENT_SELECTED"], "false")
        self.assertEqual(result["ROLE_NAME"], "script")

    def test_underscore_resource_alias_is_accepted(self):
        result = self._resolve("/nebuko update circuit_breaker --value 35")
        self.assertEqual(result["UPDATE_CIRCUIT_BREAKER"], "true")
        self.assertEqual(result["UPDATE_CIRCUIT_BREAKER_VALUE"], "35")

    def test_zero_value_is_rejected_with_usage(self):
        result = self._resolve("/nebuko update circuit-breaker --value 0")
        self.assertNotIn("UPDATE_CIRCUIT_BREAKER", result)
        self.assertEqual(result["COMMAND_ERROR"], "true")
        self.assertEqual(result["SKIP_AGENT"], "true")

    def test_non_numeric_value_is_rejected_with_usage(self):
        # Malformed commands route through the generic command-error path
        # (issue #804): a usage notice, never an agent.
        result = self._resolve("/nebuko update circuit-breaker --value abc")
        self.assertNotIn("UPDATE_CIRCUIT_BREAKER", result)
        self.assertEqual(result["COMMAND_ERROR"], "true")

    def test_missing_value_is_not_a_command(self):
        result = self._resolve("/nebuko update circuit-breaker")
        self.assertNotIn("UPDATE_CIRCUIT_BREAKER", result)

    def test_unknown_resource_is_not_a_command(self):
        result = self._resolve("/nebuko update workflow --value 35")
        self.assertNotIn("UPDATE_CIRCUIT_BREAKER", result)

    def test_bot_trigger_does_not_route_to_update(self):
        # A bot raising its own budget would be a self-rearming loop.
        result = self._resolve(
            "<!-- bot-workflow-terminal -->\n"
            "/nebuko update circuit-breaker --value 35"
        )
        self.assertNotIn("UPDATE_CIRCUIT_BREAKER", result)


class TestForceNextStepSubWorkflow(unittest.TestCase):
    """force-next-step must run a step regardless of its shape (issue #599).

    A sub-workflow step (``workflow:`` / ``repo:`` / ``path:``) has no inline
    ``agent``; force-next-step must reuse the happy-path child-entry
    resolution and point at the child workflow's first step with correct
    parent fields so return-to-parent works.
    """

    @staticmethod
    def _child_workflow_yaml(name="child", first_agent="context", second_agent="dev"):
        lines = [
            f"name: {name}",
            "description: Child workflow",
            "steps:",
            f"  - name: first_step",
            f"    agent: {first_agent}",
            "    next: second_step",
            f"  - name: second_step",
            f"    agent: {second_agent}",
            "    next: null",
        ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _subparent_workflow_yaml(sub_name="subtask", sub_ref="child"):
        return (
            "name: subparent\n"
            "description: Parent with a sub-workflow step\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: subtask\n"
            f"  - name: {sub_name}\n"
            f"    workflow: {sub_ref}\n"
            "    next: finalize\n"
            "  - name: finalize\n"
            "    agent: architect\n"
            "    next: null\n"
        )

    def _setup_dirs(self, parent_yaml, child_yaml=None, child_name="child",
                   parent_name="subparent"):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, f"{parent_name}.yaml"), "w") as f:
            f.write(parent_yaml)
        if child_yaml is not None:
            with open(os.path.join(workflows_dir, f"{child_name}.yaml"), "w") as f:
                f.write(child_yaml)
        return prompt_base, workflows_dir

    def _resolve(self, body, state_json, prompt_base, workflows_dir):
        full_body = body + "\n\n" + state_json
        return resolve_config(full_body, prompt_base, workflows_dir, is_bot=False)

    def _state(self, workflow="subparent", step="analyze"):
        import json as _json
        state = {"version": 1, "workflow": workflow, "step": step, "agent": "architect"}
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def test_explicit_target_sub_workflow_step(self):
        prompt_base, workflows_dir = self._setup_dirs(
            self._subparent_workflow_yaml(), self._child_workflow_yaml()
        )
        result = self._resolve(
            "/nebuko force-step step --name subtask",
            self._state(step="analyze"),
            prompt_base,
            workflows_dir,
        )
        # Points at the child's first step with the child workflow as active
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "context")
        self.assertEqual(result["ROLE_NAME"], "context")
        self.assertEqual(result["WORKFLOW_NAME"], "child")
        self.assertEqual(result["WORKFLOW_STEP"], "first_step")
        # Parent fields mirror the happy-path entry contract
        self.assertEqual(result["PARENT_WORKFLOW"], "subparent")
        self.assertEqual(result["PARENT_WORKFLOW_STEP"], "subtask")

    def test_agent_target_unchanged_in_subparent(self):
        # Regression: a plain agent step target in the same workflow is
        # unaffected (WORKFLOW_NAME stays the parent workflow).
        prompt_base, workflows_dir = self._setup_dirs(
            self._subparent_workflow_yaml(), self._child_workflow_yaml()
        )
        result = self._resolve(
            "/nebuko force-step step --name finalize",
            self._state(step="analyze"),
            prompt_base,
            workflows_dir,
        )
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "architect")
        self.assertEqual(result["WORKFLOW_NAME"], "subparent")
        self.assertEqual(result["WORKFLOW_STEP"], "finalize")
        self.assertEqual(result["PARENT_WORKFLOW"], "")

    def test_missing_child_workflow(self):
        prompt_base, workflows_dir = self._setup_dirs(
            self._subparent_workflow_yaml(sub_ref="does_not_exist"), child_yaml=None
        )
        result = self._resolve(
            "/nebuko force-step step --name subtask",
            self._state(step="analyze"),
            prompt_base,
            workflows_dir,
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("not found", result["REASON"])

    def test_unknown_agent_in_child_first_step(self):
        bad_child = (
            "name: badchild\n"
            "description: Child with unknown agent\n"
            "steps:\n"
            "  - name: first_step\n"
            "    agent: not_a_real_agent\n"
            "    next: null\n"
        )
        prompt_base, workflows_dir = self._setup_dirs(
            self._subparent_workflow_yaml(sub_ref="badchild"), bad_child,
            child_name="badchild",
        )
        result = self._resolve(
            "/nebuko force-step step --name subtask",
            self._state(step="analyze"),
            prompt_base,
            workflows_dir,
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("Unknown agent", result["REASON"])

    def test_script_step_target_runs_script(self):
        script_parent = (
            "name: scriptparent\n"
            "description: Parent with a script step\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: run_check\n"
            "  - name: run_check\n"
            "    script:\n"
            "      command: scripts/check.py\n"
            "      args: [\"--fast\"]\n"
            "      timeout_minutes: 5\n"
            "    next: finalize\n"
            "  - name: finalize\n"
            "    agent: architect\n"
            "    next: null\n"
        )
        prompt_base, workflows_dir = self._setup_dirs(
            script_parent, child_yaml=None, parent_name="scriptparent"
        )
        result = self._resolve(
            "/nebuko force-step step --name run_check",
            self._state(workflow="scriptparent", step="analyze"),
            prompt_base,
            workflows_dir,
        )
        # build_result short-circuits to the script execution contract
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["RUN_SCRIPT"], "true")
        self.assertEqual(result["ROLE_NAME"], "script")
        self.assertEqual(result["SCRIPT_PATH"], "scripts/check.py")
        self.assertEqual(result["SCRIPT_ARGS"], '["--fast"]')
        self.assertEqual(result["SCRIPT_TIMEOUT_MINUTES"], "5")
        self.assertEqual(result["WORKFLOW_NAME"], "scriptparent")
        self.assertEqual(result["WORKFLOW_STEP"], "run_check")
        self.assertEqual(result["PARENT_WORKFLOW"], "")


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
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: context\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: architect\n"
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
            "agent": "architect",
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
                "/nebuko force-step step --name context",
                prompt_base,
                workflows_dir,
                is_bot=False,
                repo="owner/repo",
                issue_number=42,
            )

        self.assertEqual(result["SKIP_AGENT"], "false")
        # analyze.next = context -> agent context
        self.assertEqual(result["TASK"], "context")

    def test_fallback_no_state_in_comments_returns_error(self):
        """When comments have no workflow state, still returns error."""
        from unittest.mock import patch

        prompt_base, workflows_dir = self._setup_dirs()
        comments = [{"body": "No state here", "id": 111}]

        with patch("resolve_config.get_issue_comments", return_value=comments):
            result = resolve_config(
                "/nebuko force-step step --name context",
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
            "/nebuko force-step step --name context",
            prompt_base,
            workflows_dir,
            is_bot=False,
            repo=None,
            issue_number=None,
        )

        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("no workflow state found", result["REASON"])

    def test_fallback_reads_a_bounded_tail(self):
        """The comment fallback must not re-paginate the whole thread (issue #805)."""
        from unittest.mock import patch

        from nebuko.engine.lib.thread_snapshot import state_tail_limit

        prompt_base, workflows_dir = self._setup_dirs()
        state_block = self._build_state_block(workflow="default", step="analyze")
        comments = self._comments_with_state(state_block)

        with patch(
            "resolve_config.get_issue_comments", return_value=comments
        ) as fetch:
            resolve_config(
                "/nebuko force-step step --name context",
                prompt_base,
                workflows_dir,
                is_bot=False,
                repo="owner/repo",
                issue_number=42,
            )

        fetch.assert_called_once_with("owner/repo", 42, limit=state_tail_limit())


class TestWorkflowDrivenResolution(unittest.TestCase):
    """Tests for workflow-driven resolution in resolve_config() agent_config branch."""

    @staticmethod
    def _default_workflow_yaml():
        return (
            "name: default\n"
            "description: Standard workflow\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: context\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: architect\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    @staticmethod
    def _build_state_json(workflow="default", step="analyze", agent="architect"):
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
                "version": 1, "workflow": "default", "step": "analyze", "agent": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
                "prefs": {"complexity": "medium"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # After "analyze", next step is "context" with agent "context"
        self.assertEqual(result["TASK"], "context")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_workflow_driven_resolution_with_branch_result(self):
        """Workflow-driven resolution routes on the outcome's appreciation."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "default", "step": "analyze", "agent": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # Linear resolution (no conditions in default workflow) — follows "next" field
        self.assertEqual(result["TASK"], "context")

    def test_terminal_step_returns_skip(self):
        """Terminal step with no task_hint → resolve_next_from_workflow returns None → terminal skip."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        # "review" is terminal (next: null) — resolve_next_from_workflow returns None
        # and the skip reason names the terminal step (issue #458).
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "default", "step": "review", "agent": "review",
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("Terminal step 'review'", result["REASON"])

    def test_fallback_to_agent_hint_when_no_workflow_state(self):
        """When no workflow state exists, fall back to the hand-off target."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 6,
                "turn_outcome": {
                    "status": "completed", "appreciation": "approved",
                },
                "function_call": {"function": "call_agent", "agent_name": "dev"},
                "prefs": {"complexity": "high"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["COMPLEXITY"], "high")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_backward_compatibility_simple_agent_run_config(self):
        """A state carrying only an outcome + prefs still resolves (issue #964)."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        body = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 6,
                "turn_outcome": {
                    "status": "completed", "appreciation": "approved",
                },
                "function_call": {"function": "call_agent", "agent_name": "dev"},
                "prefs": {"complexity": "low", "temperature": 0.5},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["COMPLEXITY"], "low")
        # ``reasoning`` was a legacy sentinel-only knob with no RunPrefs
        # counterpart (issue #964): the resolver now always reports "none".
        self.assertEqual(result["LLM_REASONING"], "none")
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
                "version": 1, "workflow": "default", "step": "plan", "agent": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
                "prefs": {"complexity": "medium"},
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
                "version": 1, "workflow": "nonexistent", "step": "analyze", "agent": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # resolve_next_from_workflow returns None for missing workflow when agent_hint=None
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("REASON", result)

    def test_approved_outcome_default_workflow_analyze_to_context(self):
        """Regression #377: an approved outcome with no task field on the default workflow resolves analyze→context.

        Reproduces the exact scenario from the issue: architect finishes the 'analyze' step
        with an approval (no explicit task). The resolver should parse the workflow
        state, find the next step in default.yaml ('context'), and select agent 'context'.
        """
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 1, "workflow": "default", "step": "analyze", "agent": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
                "prefs": {"complexity": "medium"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "context")
        self.assertEqual(result["SKIP_AGENT"], "false")


class TestWorkflowNextStepEmptyStepRecovery(unittest.TestCase):
    """Regression (issue #458): states with step='' but populated history must
    still resolve the next step instead of dead-ending with a generic failure."""

    @staticmethod
    def _spec_driven_impl_yaml():
        return (
            "name: spec-driven-impl\n"
            "description: Spec-driven development\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: context\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: architect\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: null\n"
        )

    def _setup_spec_dirs(self):
        import os as _os
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(_os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(_os.path.join(workflows_dir, "spec-driven-impl.yaml"), "w") as f:
            f.write(self._spec_driven_impl_yaml())
        return prompt_base, workflows_dir

    def test_resolve_workflow_next_step_recovers_from_empty_step(self):
        """Exact #457 v2 shape: workflow='spec-driven-impl', step='' (omitted),
        history ending at 'context', no turn outcome → must resolve to plan/architect."""
        import json as _json
        prompt_base, workflows_dir = self._setup_spec_dirs()
        state = {
            "version": 2,
            "workflow": "spec-driven-impl",
            "agent": "context",
            "history": [
                _history_entry("analyze", "architect", "Analyzed"),
                _history_entry("context", "context", "Collected"),
            ],
        }
        # No "step" key — excluded by exclude_defaults serialization
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result.get("TASK"), "architect")
        self.assertEqual(result.get("ROLE_NAME"), "architect")
        self.assertEqual(result.get("WORKFLOW_STEP"), "plan")
        self.assertEqual(result.get("SKIP_AGENT"), "false")

    def test_terminal_step_distinct_reason(self):
        """Genuine terminal step (no next) with empty scalar step yields the
        terminal-specific reason, not the generic failure reason."""
        import json as _json
        prompt_base, workflows_dir = self._setup_spec_dirs()
        state = {
            "version": 4,
            "workflow": "spec-driven-impl",
            "agent": "dev",
            "history": [
                _history_entry("analyze", "architect", "A"),
                _history_entry("context", "context", "B"),
                _history_entry("plan", "architect", "C"),
                _history_entry("implement", "dev", "D"),
            ],
        }
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("Terminal step 'implement'", result["REASON"])

    def test_undeterminable_current_step_distinct_reason(self):
        """No step anywhere (empty scalar, empty history) → resolution-failed
        reason, not the terminal reason."""
        import json as _json
        prompt_base, workflows_dir = self._setup_spec_dirs()
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({"version": 2, "workflow": "spec-driven-impl", "agent": "context"})
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("could not determine current step", result["REASON"])

    def _state_json(self, state: dict) -> str:
        import json as _json
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def test_step_not_found_in_workflow_distinct_reason(self):
        """Step known but absent from the workflow definition → not-found
        reason, not terminal / undeterminable."""
        prompt_base, workflows_dir = self._setup_spec_dirs()
        state_json = self._state_json(
            {
                "version": 3,
                "workflow": "spec-driven-impl",
                "step": "nonexistent",
                "history": [
                    _history_entry("analyze", "architect", "A"),
                    _history_entry("context", "context", "B"),
                    _history_entry("nonexistent", "dev", "C"),
                ],
            }
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("step 'nonexistent' not found in workflow", result["REASON"])

    def test_unresolvable_next_reference_distinct_reason(self):
        """Step exists with `next` pointing at a missing target → unresolvable
        reference reason, not terminal."""
        import os as _os
        import tempfile as _tempfile
        prompt_base = _tempfile.mkdtemp()
        workflows_dir = _tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(_os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(_os.path.join(workflows_dir, "broken.yaml"), "w") as f:
            f.write(
                "name: broken\n"
                "description: Dangling next reference\n"
                "steps:\n"
                "  - name: only\n"
                "    agent: architect\n"
                "    next: ghost\n"
            )
        state_json = self._state_json(
            {
                "version": 2,
                "workflow": "broken",
                "step": "only",
                "history": [_history_entry("only", "architect", "A")],
            }
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("does not resolve", result["REASON"])

    def test_missing_workflow_file_distinct_reason(self):
        """Workflow name set but file absent → missing-file reason."""
        import os as _os
        import tempfile as _tempfile
        prompt_base = _tempfile.mkdtemp()
        workflows_dir = _tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(_os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        state_json = self._state_json(
            {
                "version": 2,
                "workflow": "does-not-exist",
                "step": "analyze",
                "history": [_history_entry("analyze", "architect", "A")],
            }
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("workflow file 'does-not-exist.yaml' not found", result["REASON"])

    def test_malformed_workflow_yaml_distinct_reason(self):
        """Malformed workflow YAML (not a mapping) → load-failure reason
        instead of an unhandled exception."""
        import os as _os
        import tempfile as _tempfile
        prompt_base = _tempfile.mkdtemp()
        workflows_dir = _tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(_os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(_os.path.join(workflows_dir, "malformed.yaml"), "w") as f:
            f.write("- just\n- a\n- list\n")
        state_json = self._state_json(
            {
                "version": 2,
                "workflow": "malformed",
                "step": "analyze",
                "history": [_history_entry("analyze", "architect", "A")],
            }
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("could not load workflow 'malformed'", result["REASON"])


class TestE2EDispatchWorkflowResolution(unittest.TestCase):
    """End-to-end: agent finishes with an approved outcome → resolver uses workflow YAML."""

    @staticmethod
    def _approval_workflow_yaml():
        return (
            "name: approval\n"
            "description: Approval workflow with conditional branching\n"
            "steps:\n"
            "  - name: spec\n"
            "    agent: po\n"
            "    next: spec_approval\n"
            "  - name: spec_approval\n"
            "    agent: architect\n"
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
    def _build_state_json(workflow, step, agent, outcome=None, required_agent=None):
        import json as _json
        state = {
            "version": 6,
            "workflow": workflow,
            "step": step,
            "agent": agent,
        }
        if outcome:
            state["turn_outcome"] = {
                "status": "completed",
                "appreciation": "approved",
                "required_agent": required_agent or "",
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

    def test_approved_outcome_resolves_correct_next_agent(self):
        """Agent finishes with an approval; resolver picks up next agent from workflow."""
        prompt_base, workflows_dir = self._setup_dirs()
        # Agent "po" finished step "spec", the approval advances to the next step
        state_json = self._build_state_json(
            workflow="approval", step="spec", agent="po",
            outcome="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # After "spec", next step is "spec_approval" with agent "architect"
        self.assertEqual(result["TASK"], "architect")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_approved_outcome_without_task_field(self):
        """Regression: an outcome-only advance (no task field) must resolve next agent from workflow."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="approval", step="spec", agent="po",
            outcome="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # After "spec", next step is "spec_approval" with agent "architect"
        self.assertEqual(result["TASK"], "architect")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_approved_outcome_with_branch_approved(self):
        """An approved verdict resolves to the implement step."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 6, "workflow": "approval", "step": "spec_approval", "agent": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # route_key="approved" → condition routes to "implement" → agent "dev"
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_rejected_outcome_loops_back_to_spec(self):
        """A rejected verdict loops back to the spec step."""
        prompt_base, workflows_dir = self._setup_dirs()
        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 6, "workflow": "approval", "step": "spec_approval", "agent": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "rejected"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # route_key="rejected" → condition routes to "spec" → agent "po"
        self.assertEqual(result["TASK"], "po")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_full_workflow_simulation_through_resolver(self):
        """Simulate a full workflow run through resolve_config with mock workflow state."""
        prompt_base, workflows_dir = self._setup_dirs()

        # Step 1: spec → spec_approval
        state = self._build_state_json(
            "approval", "spec", "po",
            outcome="advance",
        )
        result = resolve_config(state, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "architect")
        self.assertEqual(result["ROLE_NAME"], "architect")

        # Step 2: spec_approval (approved) → implement
        import json as _json
        state = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({
                "version": 6, "workflow": "approval", "step": "spec_approval", "agent": "architect",
                "turn_outcome": {"status": "completed", "appreciation": "approved"},
            })
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(state, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["ROLE_NAME"], "dev")

        # Step 3: implement → review
        state = self._build_state_json(
            "approval", "implement", "dev",
            outcome="advance",
        )
        result = resolve_config(state, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "review")
        self.assertEqual(result["ROLE_NAME"], "review")

        # Step 4: review (terminal) → SKIP
        state = self._build_state_json(
            "approval", "review", "review",
            outcome="advance",
        )
        result = resolve_config(state, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["SKIP_AGENT"], "true")


class TestDispatchAdvanceDefaultWorkflow(unittest.TestCase):
    """E2E: an approved outcome with the default workflow resolves next agent from workflow YAML.

    Regression test for issue #377 — ensures the exact body format from the issue
    (WORKFLOW_STATE + an approved outcome without task field) correctly resolves
    to the context agent when the current step is 'analyze' in the 'default' workflow.
    """

    @staticmethod
    def _default_workflow_yaml():
        return (
            "name: default\n"
            "description: Standard workflow\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: context\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: architect\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    @staticmethod
    def _build_state_json(workflow="default", step="analyze", agent="architect",
                          outcome=None, required_agent=None):
        import json as _json
        state = {
            "version": 6,
            "workflow": workflow,
            "step": step,
            "agent": agent,
        }
        if outcome:
            state["turn_outcome"] = {
                "status": "completed",
                "appreciation": "approved",
                "required_agent": required_agent or "",
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

    def test_approved_outcome_analyze_resolves_to_context(self):
        """Exact scenario from issue #377: an approval from analyze → context."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="default", step="analyze", agent="architect",
            outcome="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # After "analyze" in default workflow, next step is "context" → agent "context"
        self.assertEqual(result["TASK"], "context")
        self.assertEqual(result["SKIP_AGENT"], "false")
        # Regression #443: workflow metadata must be populated for state propagation
        self.assertEqual(result["WORKFLOW_NAME"], "default")
        self.assertEqual(result["WORKFLOW_STEP"], "context")
        self.assertEqual(result["WORKFLOW_NEXT"], "plan")

    def test_approved_outcome_context_resolves_to_architect(self):
        """An approval from the context step → architect (plan step)."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="default", step="context", agent="context",
            outcome="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "architect")
        self.assertEqual(result["SKIP_AGENT"], "false")
        # Regression #443: workflow metadata must be populated for state propagation
        self.assertEqual(result["WORKFLOW_NAME"], "default")
        self.assertEqual(result["WORKFLOW_STEP"], "plan")
        self.assertEqual(result["WORKFLOW_NEXT"], "implement")

    def test_approved_outcome_implement_resolves_to_dev(self):
        """An approval from the plan step → dev (implement step)."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="default", step="plan", agent="architect",
            outcome="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["SKIP_AGENT"], "false")
        # Regression #443: workflow metadata must be populated for state propagation
        self.assertEqual(result["WORKFLOW_NAME"], "default")
        self.assertEqual(result["WORKFLOW_STEP"], "implement")
        self.assertEqual(result["WORKFLOW_NEXT"], "review")

    def test_approved_outcome_terminal_step_returns_skip(self):
        """An approval from the terminal review step → SKIP_AGENT."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json(
            workflow="default", step="review", agent="review",
            outcome="advance",
        )
        result = resolve_config(state_json, prompt_base, workflows_dir, is_bot=False)
        # Terminal step: resolve_next_from_workflow returns None,
        # the approval falls back to an empty task_hint → no valid task
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

    def _build_state_json(self, workflow="default", step="analyze", agent="architect"):
        import json as _json
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps({"version": 1, "workflow": workflow, "step": step, "agent": agent})
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def test_force_next_step_with_workflow_driven_no_conflict(self):
        """force-next-step works correctly when there's no agentRunConfig (human command)."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json("default", "analyze", "architect")
        body = "/nebuko force-step step --name context\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # Auto-advance: analyze.next = context → agent context
        self.assertEqual(result["TASK"], "context")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_force_next_step_explicit_target_with_workflow_driven(self):
        """force-next-step with explicit target overrides normal workflow progression."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json("default", "analyze", "architect")
        body = "/nebuko force-step step --name implement\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # Explicit target "implement" → agent dev (bypasses normal analyze→context flow)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["SKIP_AGENT"], "false")


class TestHumanWorkflowOverridePrecedence(unittest.TestCase):
    """Human /nebuko command takes precedence over workflow state."""

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
                "steps:\n"
                "  - name: review\n"
                "    agent: review\n"
                "    next: null\n"
            )
        return prompt_base, workflows_dir

    def test_workflow_command_precedence_over_workflow_state(self):
        """/nebuko command takes precedence even when workflow state exists."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + '{"version": 1, "workflow": "default", "step": "analyze", "agent": "architect"}'
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        body = "/nebuko run workflow --name review-only\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # /nebuko review-only should take precedence and resolve to first agent of review-only
        self.assertEqual(result["ROLE_NAME"], "review")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_workflow_command_precedence_over_mention(self):
        """/nebuko command takes precedence over @@mention."""
        prompt_base, workflows_dir = self._setup_dirs()
        body = "/nebuko run workflow --name default\n@@dev please implement something"
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # /nebuko default triggers the workflow — architect is the trigger agent
        self.assertEqual(result["ROLE_NAME"], "architect")
        self.assertEqual(result["SKIP_AGENT"], "false")


class TestResolveConfigWithRealWorkflowFile(unittest.TestCase):
    """Integration tests using the real workflows/default.yaml file from the repo root.

    Regression for issue #377 — ensures the full resolve_config pipeline correctly
    parses WORKFLOW_STATE markers, an approved outcome without task field, and resolves
    the next agent against the actual workflow definition on disk.
    """

    @staticmethod
    def _real_workflows_dir():
        """The shipped workflow dir, not the per-machine ``.nebuko`` override."""
        return _real_workflows_dir()

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
        """Reproduce the body from issue #377 using a turn outcome in workflow state.

        Contains:
        - WORKFLOW_STATE block: workflow=default, step=analyze, agent=architect,
          with an approved turn outcome (no task field)
        Expected: resolves to agent 'context'.
        """
        return (
            "<summary>🤖 Workflow State (v1)</summary>\n"
            "\n"
            "```json\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"workflow": "default", "step": "analyze", "agent": "architect", "phase": "analysis", '
            '"turn_outcome": {"status": "completed", "appreciation": "approved"}, '
            '"history": [{"step": "analyze", "agent": "architect", "summary": "delegate context collection", '
            '"turn_outcome": {"status": "completed", "appreciation": "approved"}}], '
            '"metrics": {"run_id": "d17d26d4-c4de-4808-b80b-e353d79e0563", "model_name": "default", '
            '"agent_role": "architect", "input_tokens": 8069, "output_tokens": 276, '
            '"tool_call_latency_ms": 9842.795133590698, "agent_runtime_ms": 9842.8}, '
            '"issue": {"number": 377, "url": "https://github.com/olivier-norture/git-flow-ai/issues/377", '
            '"title": "fix workflow force-next-step requires an active workflow", '
            '"author": "olivier-norture", "repo": "olivier-norture/git-flow-ai"}}\n'
            "<!-- WORKFLOW_STATE_END -->\n"
            "```\n"
            "\n"
            "</details>\n"
            "<!-- bot-agent-architect -->\n"
            "*Architect answer*\n"
            "\n"
            "I need to understand the code flow for `force-next-step`.\n"
        )

    def test_exact_issue_input_resolves_to_context(self):
        """Exact scenario from issue #377: an approval from analyze → context agent.

        The body contains a WORKFLOW_STATE (workflow=default, step=analyze, agent=architect)
        and an agentRunConfig with an approval but NO task field.
        resolve_config should parse the state, look up the next step in the real
        workflows/default.yaml, and resolve to agent 'context'.
        """
        prompt_base = self._setup_prompt_base()
        workflows_dir = str(self._real_workflows_dir())
        body = self._build_issue_body()

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=True)

        self.assertEqual(result["TASK"], "context",
            "an approval from the 'analyze' step in default workflow should resolve to 'context' agent")
        self.assertEqual(result["SKIP_AGENT"], "false")



class TestResumeIntegration(unittest.TestCase):
    """Integration tests for /nebuko resume command."""

    @staticmethod
    def _setup_prompt_base():
        import tempfile
        prompt_base = tempfile.mkdtemp()
        os.makedirs(os.path.join(prompt_base, "dev"), exist_ok=True)
        os.makedirs(os.path.join(prompt_base, "architect"), exist_ok=True)
        os.makedirs(os.path.join(prompt_base, "context"), exist_ok=True)
        os.makedirs(os.path.join(prompt_base, "review"), exist_ok=True)
        for role in ("dev", "architect", "context", "review"):
            with open(os.path.join(prompt_base, role, "system.md"), "w") as f:
                f.write(f"# {role} prompt")
        return prompt_base

    @staticmethod
    def _default_workflow_yaml():
        return (
            "name: default\n"
            "description: Standard workflow\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: architect\n"
            "    next: context\n"
            "  - name: context\n"
            "    agent: context\n"
            "    next: plan\n"
            "  - name: plan\n"
            "    agent: architect\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    def _build_state_json(self, workflow, step, agent, outcome="hold", **kwargs):
        import json
        data = {
            "version": 6,
            "workflow": workflow,
            "step": step,
            "agent": agent,
            "phase": step,
            "history": [],
            "prefs": {"complexity": kwargs.get("complexity", "high")},
        }
        if outcome == "hold":
            data["turn_outcome"] = {
                "status": "completed",
                "appreciation": "hold",
                "hold_kind": "awaiting_input",
            }
        return json.dumps(data)

    def _build_trigger_body_with_state(self, command, state_json):
        lines = [
            command,
            "<details>",
            "<summary>🤖 Workflow State (v1)</summary>",
            "",
            "```json",
            "<!-- WORKFLOW_STATE_START -->",
            state_json,
            "<!-- WORKFLOW_STATE_END -->",
            "```",
            "",
            "</details>",
        ]
        return "\n".join(lines)

    def test_resume_reruns_held_step(self):
        """Resume should re-run the same held step, not advance."""
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = os.path.join(tmpdir, "default.yaml")
            with open(wf_path, "w") as f:
                f.write(self._default_workflow_yaml())

            state_json = self._build_state_json(
                workflow="default", step="implement", agent="dev",
                outcome="hold", complexity="high",
            )
            body = self._build_trigger_body_with_state("/nebuko resume --feedback Yes, approved.", state_json)

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            self.assertEqual(result["SKIP_AGENT"], "false")
            self.assertEqual(result["TASK"], "dev")
            self.assertEqual(result["WORKFLOW_STEP"], "implement")
            self.assertEqual(result["COMPLEXITY"], "high")
            self.assertEqual(result["HUMAN_RESUME_INPUT"], "Yes, approved.")

    def test_resume_without_input(self):
        """Bare resume command (no input text) still works and carries the
        BARE_RESUME_INPUT sentinel so human_gate steps recognize the run as
        human-triggered (issue #506)."""
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = os.path.join(tmpdir, "default.yaml")
            with open(wf_path, "w") as f:
                f.write(self._default_workflow_yaml())

            state_json = self._build_state_json(
                workflow="default", step="implement", agent="dev",
                outcome="hold", complexity="medium",
            )
            body = self._build_trigger_body_with_state("/nebuko resume", state_json)

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            self.assertEqual(result["SKIP_AGENT"], "false")
            self.assertEqual(result["TASK"], "dev")
            self.assertEqual(result["HUMAN_RESUME_INPUT"], BARE_RESUME_INPUT)

    def test_resume_with_approved_outcome_advances_to_next_step(self):
        """Resume on a non-resumable (approved) outcome advances, not skips."""
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = os.path.join(tmpdir, "default.yaml")
            with open(wf_path, "w") as f:
                f.write(self._default_workflow_yaml())

            state_json = self._build_state_json(
                workflow="default", step="plan", agent="architect",
                outcome="advance",
            )
            body = self._build_trigger_body_with_state("/nebuko resume", state_json)

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            # An approved outcome routes through _resolve_outcome_path, which
            # auto-resolves the next agent from the workflow (plan → implement/dev).
            self.assertEqual(result["SKIP_AGENT"], "false")
            self.assertEqual(result["TASK"], "dev")

    def test_resume_terminal_step(self):
        """Resuming a terminal step (next=null) should still re-run that step."""
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = os.path.join(tmpdir, "default.yaml")
            with open(wf_path, "w") as f:
                f.write(self._default_workflow_yaml())

            state_json = self._build_state_json(
                workflow="default", step="review", agent="review",
                outcome="hold", complexity="low",
            )
            body = self._build_trigger_body_with_state(
                "/nebuko resume --feedback Looks good, let me double-check.", state_json
            )

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            self.assertEqual(result["SKIP_AGENT"], "false")
            self.assertEqual(result["TASK"], "review")
            self.assertEqual(result["HUMAN_RESUME_INPUT"], "Looks good, let me double-check.")

    def test_resume_invalid_workflow_name(self):
        """Resume with invalid workflow name should be rejected."""
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_json = self._build_state_json(
                workflow="../evil", step="plan", agent="architect",
                outcome="hold",
            )
            body = self._build_trigger_body_with_state("/nebuko resume", state_json)

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            self.assertEqual(result["SKIP_AGENT"], "true")
            self.assertIn("invalid or missing workflow", result["REASON"])

    def test_resume_preserves_run_prefs(self):
        """Resume should copy the run prefs (model, temperature, etc.)."""
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = os.path.join(tmpdir, "default.yaml")
            with open(wf_path, "w") as f:
                f.write(self._default_workflow_yaml())

            import json
            data = {
                "version": 6,
                "workflow": "default",
                "step": "plan",
                "agent": "architect",
                "phase": "plan",
                "history": [],
                "turn_outcome": {
                    "status": "completed",
                    "appreciation": "hold",
                    "hold_kind": "awaiting_input",
                },
                "prefs": {
                    "complexity": "high",
                    "model": "claude-sonnet-4-20250514",
                    "temperature": 0.5,
                    "top_p": 0.9,
                },
            }
            state_json = json.dumps(data)
            body = self._build_trigger_body_with_state("/nebuko resume --feedback OK", state_json)

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            self.assertEqual(result["SKIP_AGENT"], "false")
            self.assertEqual(result["CONFIG_MODEL"], "claude-sonnet-4-20250514")
            self.assertEqual(result["CONFIG_TEMPERATURE"], "0.5")
            self.assertEqual(result["CONFIG_TOP_P"], "0.9")

    def test_resume_no_workflow_state(self):
        """Resume with no workflow state should not crash (falls through to human path)."""
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = os.path.join(tmpdir, "default.yaml")
            with open(wf_path, "w") as f:
                f.write(self._default_workflow_yaml())

            body = "/nebuko resume"

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            # Bare resume without workflow state falls through the cascade (resume
            # requires a prev_state) to the human path. We just verify the result
            # doesn't contain a crash-level error.
            self.assertIsInstance(result, dict)

    def _spec_driven_workflow_yaml(self):
        """Minimal spec-driven workflow YAML matching workflows/spec-driven.yaml."""
        return (
            "name: spec-driven\n"
            "description: Spec-driven workflow\n"
            "steps:\n"
            "  - name: spec\n"
            "    agent: po\n"
            "    next: spec_approval\n"
            "  - name: spec_approval\n"
            "    agent: architect\n"
            "    next: architect\n"
            "  - name: architect\n"
            "    agent: architect\n"
            "    next: implement\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )

    def _build_state_json_no_outcome(self, workflow, step, agent, **kwargs):
        """Build workflow state JSON without a turn outcome (resolution-only).

        Mirrors the WorkflowState produced by agents that post resolution.next_step
        but do not include a turn outcome — the exact scenario from issue #420.
        """
        import json
        data = {
            "version": kwargs.get("version", 1),
            "workflow": workflow,
            "step": step,
            "agent": agent,
            "phase": step,
            "history": kwargs.get("history", []),
        }
        if "resolution" in kwargs:
            data["resolution"] = kwargs["resolution"]
        return json.dumps(data)

    def test_resume_with_no_outcome_advances_to_next_step(self):
        """Resume with no turn outcome should advance to next step, not SKIP_AGENT.

        Reproduces issue #420: when an agent posts WorkflowState with resolution
        but no turn outcome, issuing /nebuko resume should advance to the next step
        defined in the workflow YAML (not skip with 'step is not on hold').
        """
        prompt_base = self._setup_prompt_base()
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = os.path.join(tmpdir, "default.yaml")
            with open(wf_path, "w") as f:
                f.write(self._default_workflow_yaml())

            state_json = self._build_state_json_no_outcome(
                workflow="default", step="analyze", agent="architect",
                resolution={"task": "architect", "next_step": "context"},
            )
            body = self._build_trigger_body_with_state(
                "/nebuko resume --feedback Here is my feedback.", state_json
            )

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            self.assertEqual(result["SKIP_AGENT"], "false")
            self.assertEqual(result["TASK"], "context")
            self.assertEqual(result["WORKFLOW_STEP"], "context")

    def test_resume_no_outcome_with_spec_driven_workflow(self):
        """Resume with no turn outcome using spec-driven workflow (exact issue #420 scenario).

        PO agent completes spec step, posts resolution.next_step='spec_approval'
        with no turn outcome. User issues /nebuko resume with answers. Expected:
        advance to spec_approval step with architect agent.
        """
        prompt_base = self._setup_prompt_base()
        os.makedirs(os.path.join(prompt_base, "po"), exist_ok=True)
        with open(os.path.join(prompt_base, "po", "system.md"), "w") as f:
            f.write("# po prompt")

        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = os.path.join(tmpdir, "spec-driven.yaml")
            with open(wf_path, "w") as f:
                f.write(self._spec_driven_workflow_yaml())

            state_json = self._build_state_json_no_outcome(
                workflow="spec-driven", step="spec", agent="po",
                resolution={
                    "task": "po",
                    "input_workflow": "spec-driven",
                    "input_step": "spec",
                    "next_step": "spec_approval",
                    "reasoning": "none",
                },
            )
            body = self._build_trigger_body_with_state(
                "/nebuko resume --feedback 1. Yes 2. No 3. Use REST API.", state_json
            )

            result = resolve_config(body, prompt_base, tmpdir, is_bot=False)

            self.assertEqual(result["SKIP_AGENT"], "false")
            self.assertEqual(result["TASK"], "architect")
            self.assertEqual(result["WORKFLOW_STEP"], "spec_approval")


class TestHumanCommandPrecedenceOverOutcome(unittest.TestCase):
    """Regression tests: human commands must take precedence over auto-routing
    when an active workflow state contains a turn outcome.

    Bug: line 911 (`prev_state and outcome_obj`) was evaluated before
    `force_next`, `wf_override`, and `not is_bot`, causing all human comments
    to be absorbed by the routing path.
    """

    @staticmethod
    def _setup_dirs():
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(
                "name: default\n"
                "description: Standard workflow\n"
                "steps:\n"
                "  - name: analyze\n"
                "    agent: architect\n"
                "    next: context\n"
                "  - name: context\n"
                "    agent: context\n"
                "    next: plan\n"
                "  - name: plan\n"
                "    agent: architect\n"
                "    next: implement\n"
                "  - name: implement\n"
                "    agent: dev\n"
                "    next: review\n"
                "  - name: review\n"
                "    agent: review\n"
                "    next: null\n"
            )
        return prompt_base, workflows_dir

    @staticmethod
    def _build_state_json_with_outcome(workflow="default", step="analyze", agent="architect"):
        import json as _json
        state = {
            "version": 6,
            "workflow": workflow,
            "step": step,
            "agent": agent,
            "turn_outcome": {"status": "completed", "appreciation": "approved"},
            "prefs": {"complexity": "high"},
        }
        return (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def test_mention_with_outcome_in_body_goes_to_human_path(self):
        """@@context in body with an embedded outcome must resolve to context, not follow it."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json_with_outcome("default", "analyze", "architect")
        # Bot comment with workflow state + outcome, but human @@mention should win
        body = "@@context gather context\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        # With the fix, @@context resolves standalone via _resolve_human_path
        self.assertEqual(result["TASK"], "context")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_force_next_step_with_outcome_in_body(self):
        """/nebuko force-next-step with an embedded outcome must honor the command."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json_with_outcome("default", "analyze", "architect")
        body = "/nebuko force-step step --name implement\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_workflow_override_with_outcome_in_body(self):
        """/nebuko command with an embedded outcome must honor the override."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json_with_outcome("default", "analyze", "architect")
        body = "/nebuko run workflow --name default\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["ROLE_NAME"], "architect")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_bot_comment_with_outcome_follows_the_routing_path(self):
        """Bot comments (is_bot=True) with an outcome MUST still follow the routing path."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json_with_outcome("default", "analyze", "architect")
        body = "<!-- bot-agent-architect -->\nArchitect answer\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=True)
        # Bot comment → routing path → resolves to next step (context → context)
        self.assertEqual(result["TASK"], "context")
        self.assertEqual(result["SKIP_AGENT"], "false")

    def test_force_next_with_trailing_text_and_outcome(self):
        """/nebuko force-step with an embedded outcome must still work."""
        prompt_base, workflows_dir = self._setup_dirs()
        state_json = self._build_state_json_with_outcome("default", "analyze", "architect")
        body = "/nebuko force-step step --name implement\n\n" + state_json
        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["SKIP_AGENT"], "false")


class TestPlainTriggerNestedSubWorkflow(unittest.TestCase):
    """Plain /nebuko trigger on a workflow whose first step mounts a sub-workflow
    whose first step is itself a sub-workflow reference (three-level nesting,
    issue #609: plan-orchestration -> tmp-impl -> dev-review-loop shape).

    The trigger path must descend to the innermost runnable leaf and keep the
    outermost parent pair on record so the flat parent-pair state survives the
    nesting (the chain unwinds one level per return).
    """

    @staticmethod
    def _nested_fixtures_yaml():
        return {
            "parent-wf.yaml": (
                "name: parent-wf\n"
                "description: outer parent\n"
                "steps:\n"
                "  - name: mount\n"
                "    workflow: mid-wf\n"
                "    next: null\n"
            ),
            "mid-wf.yaml": (
                "name: mid-wf\n"
                "description: middle level\n"
                "steps:\n"
                "  - name: run\n"
                "    workflow: leaf-wf\n"
                "    skill: [mid_skills/skill_a]\n"
                "    next: null\n"
            ),
            "leaf-wf.yaml": (
                "name: leaf-wf\n"
                "description: innermost loop\n"
                "steps:\n"
                "  - name: do\n"
                "    agent: dev\n"
                "    next: check\n"
                "  - name: check\n"
                "    agent: review\n"
                "    conditions:\n"
                "      - on_result: approved\n"
                "        next: null\n"
                "      - on_result: rejected\n"
                "        next: do\n"
            ),
        }

    def _setup_nested_dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        for fname, content in self._nested_fixtures_yaml().items():
            with open(os.path.join(workflows_dir, fname), "w") as f:
                f.write(content)
        return prompt_base, workflows_dir

    def test_plain_trigger_descends_to_innermost_leaf(self):
        """A plain /nebuko trigger on a nested-first-step workflow descends to the leaf."""
        prompt_base, workflows_dir = self._setup_nested_dirs()
        body = "/nebuko run workflow --name parent-wf\n\nstart the nested plan"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["ROLE_NAME"], "dev")
        self.assertEqual(result["WORKFLOW_NAME"], "leaf-wf")
        self.assertEqual(result["WORKFLOW_STEP"], "do")
        self.assertEqual(result["WORKFLOW_NEXT"], "check")

    def test_plain_trigger_outer_parent_pair_survives(self):
        """The outermost parent pair survives the nested descent into the flat state."""
        prompt_base, workflows_dir = self._setup_nested_dirs()
        body = "/nebuko run workflow --name parent-wf\n\nstart the nested plan"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        self.assertEqual(result["PARENT_WORKFLOW"], "parent-wf")
        self.assertEqual(result["PARENT_WORKFLOW_STEP"], "mount")

    def test_plain_trigger_two_level_nesting_immediate_parent(self):
        """With only two levels, the immediate parent pair is recorded."""
        prompt_base, workflows_dir = self._setup_nested_dirs()
        # Triggering mid-wf directly: parent-wf is out of the picture, so the
        # recorded pair is the intermediate level's reference step.
        body = "/nebuko run workflow --name mid-wf\n\nstart the loop"

        result = resolve_config(body, prompt_base, workflows_dir, is_bot=False)

        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["WORKFLOW_NAME"], "leaf-wf")
        self.assertEqual(result["WORKFLOW_STEP"], "do")
        self.assertEqual(result["PARENT_WORKFLOW"], "mid-wf")
        self.assertEqual(result["PARENT_WORKFLOW_STEP"], "run")

    def test_force_next_step_exact_tmp_impl_shape(self):
        """force-next-step on the exact issue #609 shape: a generated
        workflow whose step references a nested sub-workflow (tmp-impl ->
        dev-review-loop). The originally reported failure was
        "force-next-step: Unknown agent 'None' in sub-workflow 'tmp-impl'";
        the forced entry must descend to the leaf's runnable step."""
        prompt_base, workflows_dir = self._setup_nested_dirs()
        steps = []
        for i in range(1, 7):
            nxt = f"step_{i + 1}" if i < 6 else "null"
            steps.append(
                f"  - name: step_{i}\n"
                f"    workflow: dev-review-loop\n"
                f"    skill: [tmp_impl/skills/step_{i}]\n"
                f"    next: {nxt}\n"
            )
        tmp_impl = (
            "name: tmp-impl\n"
            "description: dynamically generated per-step execution workflow\n"
            "steps:\n" + "".join(steps)
        )
        with open(os.path.join(workflows_dir, "tmp-impl.yaml"), "w") as f:
            f.write(tmp_impl)
        with open(os.path.join(workflows_dir, "dev-review-loop.yaml"), "w") as f:
            f.write(
                "name: dev-review-loop\n"
                "description: one implementation step + review loop\n"
                "steps:\n"
                "  - name: implement\n"
                "    agent: dev\n"
                "    next: review\n"
                "  - name: review\n"
                "    agent: review\n"
                "    conditions:\n"
                "      - on_result: approved\n"
                "        next: null\n"
                "      - on_result: rejected\n"
                "        next: implement\n"
            )

        import json as _json
        state_json = (
            "<!-- WORKFLOW_STATE_START -->\n"
            + _json.dumps(
                {"version": 1, "workflow": "tmp-impl", "step": "step_1",
                 "agent": "dev"}
            )
            + "\n<!-- WORKFLOW_STATE_END -->"
        )
        result = resolve_config(
            "/nebuko force-step step --name step_1\n\n" + state_json,
            prompt_base,
            workflows_dir,
            is_bot=False,
        )
        # Descends to the leaf's runnable step, not a None agent
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["ROLE_NAME"], "dev")
        self.assertEqual(result["WORKFLOW_NAME"], "dev-review-loop")
        self.assertEqual(result["WORKFLOW_STEP"], "implement")
        self.assertEqual(result["WORKFLOW_NEXT"], "review")
        # Parent pair mirrors the tmp-impl reference step
        self.assertEqual(result["PARENT_WORKFLOW"], "tmp-impl")
        self.assertEqual(result["PARENT_WORKFLOW_STEP"], "step_1")


class TestBranchRoutingRegression617:
    """Regression replay of issue #617 / #618: the plan-orchestration
    sub-workflow must start from a persisted state whose turn outcome carries
    ``appreciation: approved`` (workflow=plan-orchestration, step=plan).

    Before the fix the verdict was dropped at persistence, so the
    conditions-only ``plan`` step dead-ended into "No valid task found".
    """

    def _setup(self):
        prompt_base = tempfile.mkdtemp()
        for name in TASK_TO_PROMPT.values():
            with open(os.path.join(prompt_base, name), "w") as f:
                f.write(f"# Prompt for {name}")
        return prompt_base

    def _real_workflows_dir(self):
        """The shipped workflow dir, not the per-machine ``.nebuko`` override."""
        return _real_workflows_dir()

    def _v6_body(self, turn_outcome):
        state = {
            "version": 6,
            "workflow": "plan-orchestration",
            "step": "plan",
            "agent": "architect",
            "turn_outcome": turn_outcome,
        }
        return (
            "<!-- bot-workflow-terminal -->\n"
            "*Architect answer*\n\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            + json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->"
        )

    def test_branch_approved_routes_to_scaffold(self):
        """An approved verdict on step=plan routes to the scaffold agent,
        not a skip."""
        prompt_base = self._setup()
        body = self._v6_body(
            {"status": "completed", "appreciation": "approved",
             "note": "Re-emitted approved planWorkflow to start plan-orchestration execution."}
        )
        result = resolve_config(
            body, prompt_base, str(self._real_workflows_dir()), is_bot=True
        )
        assert result["SKIP_AGENT"] == "false"
        assert result["TASK"] == "dev"
        assert result["WORKFLOW_NAME"] == "plan-orchestration"
        assert result["WORKFLOW_STEP"] == "scaffold"

    def test_branchless_plan_step_still_skips_without_default(self):
        """Pre-fix persisted shape (an outcome with a note but no verdict) on
        the conditions-only plan step without a default condition still
        dead-ends — the verdict is what makes the primary test pass."""
        prompt_base = self._setup()
        body = self._v6_body(
            {"status": "completed", "appreciation": "hold",
             "hold_kind": "awaiting_input",
             "note": "Re-emitted approved planWorkflow to start plan-orchestration execution."}
        )
        result = resolve_config(
            body, prompt_base, str(self._real_workflows_dir()), is_bot=True
        )
        assert result["SKIP_AGENT"] == "true"
