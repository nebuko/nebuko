"""Tests for build_output module."""

import json
import unittest
from build_output import (
    agent_run_config_to_dispatch,
    is_malformed_final_message,
    _strip_agent_run_configs,
)


class TestAgentRunConfigToDispatch(unittest.TestCase):
    """Test the agent_run_config_to_dispatch() function."""

    def test_valid_minimal_json(self):
        """Minimal JSON with just task produces correct defaults."""
        data = '{"task": "ctx"}'
        result = agent_run_config_to_dispatch(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.task_hint, "ctx")
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.complexity, "medium")
        self.assertEqual(result.reasoning, "none")
        self.assertIsNone(result.model)

    def test_valid_full_json(self):
        """Full JSON with all fields produces correct Dispatch object."""
        data = json.dumps({
            "status": "loop",
            "task": "dev",
            "complexity": "high",
            "reasoning": "ultra",
            "model": "claude-opus-4-0",
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 50,
            "min_p": 0.1,
            "presence_penalty": 0.5,
            "repetition_penalty": 1.2,
            "workflow": "default",
            "step": "implement",
            "branch": "approved",
            "parent_workflow": "parent",
            "parent_workflow_step": "initiate",
            "skills": "skill1",
        })
        result = agent_run_config_to_dispatch(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "loop")
        self.assertEqual(result.task_hint, "dev")
        self.assertEqual(result.complexity, "high")
        self.assertEqual(result.reasoning, "ultra")
        self.assertEqual(result.model, "claude-opus-4-0")
        self.assertEqual(result.temperature, 0.7)
        self.assertEqual(result.top_p, 0.9)
        self.assertEqual(result.top_k, 50)
        self.assertEqual(result.min_p, 0.1)
        self.assertEqual(result.presence_penalty, 0.5)
        self.assertEqual(result.repetition_penalty, 1.2)
        self.assertEqual(result.workflow, "default")
        self.assertEqual(result.step, "implement")
        self.assertEqual(result.branch, "approved")
        self.assertEqual(result.parent_workflow, "parent")
        self.assertEqual(result.parent_workflow_step, "initiate")
        self.assertEqual(result.skills, "skill1")

    def test_invalid_json_returns_none(self):
        """Invalid JSON returns None."""
        result = agent_run_config_to_dispatch("not valid json")
        self.assertIsNone(result)

    def test_non_dict_json_returns_none(self):
        """JSON that is not a dict (e.g., array) returns None."""
        result = agent_run_config_to_dispatch('[1, 2, 3]')
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        result = agent_run_config_to_dispatch("")
        self.assertIsNone(result)

    def test_none_input_returns_none(self):
        """None input returns None."""
        result = agent_run_config_to_dispatch(None)
        self.assertIsNone(result)

    def test_missing_fields_use_defaults(self):
        """Missing optional fields use correct defaults."""
        data = '{}'
        result = agent_run_config_to_dispatch(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.complexity, "medium")
        self.assertEqual(result.reasoning, "none")
        self.assertIsNone(result.task_hint)

    def test_status_field_override(self):
        """Status field can be overridden from default."""
        data = '{"task": "ctx", "status": "terminate"}'
        result = agent_run_config_to_dispatch(data)
        self.assertEqual(result.status, "terminate")

    def test_nested_dispatch_structure(self):
        """Agents emit {\"dispatch\": {...}, \"complexity\": \"...\"} — must unwrap."""
        data = '{"dispatch": {"status": "advance"}, "complexity": "high"}'
        result = agent_run_config_to_dispatch(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.complexity, "high")

    def test_nested_dispatch_with_all_fields(self):
        """Nested dispatch with multiple fields plus top-level complexity."""
        data = '{"dispatch": {"status": "loop", "task": "dev", "workflow": "custom", "step": "review"}, "complexity": "low"}'
        result = agent_run_config_to_dispatch(data)
        self.assertEqual(result.status, "loop")
        self.assertEqual(result.task_hint, "dev")
        self.assertEqual(result.complexity, "low")
        self.assertEqual(result.workflow, "custom")
        self.assertEqual(result.step, "review")

    def test_nested_dispatch_complexity_inside_overrides_top_level(self):
        """If complexity appears both inside dispatch and at top-level, nested wins after unwrap."""
        data = '{"dispatch": {"status": "advance", "complexity": "high"}, "complexity": "low"}'
        result = agent_run_config_to_dispatch(data)
        # data.update(dispatch) puts "high" into data; data.get("complexity") sees "high"
        self.assertEqual(result.complexity, "high")

    def test_flat_format_still_works(self):
        """Backward compat: flat format without nested dispatch still works."""
        data = '{"status": "advance", "complexity": "medium"}'
        result = agent_run_config_to_dispatch(data)
        self.assertEqual(result.status, "advance")
        self.assertEqual(result.complexity, "medium")


class TestStripAgentRunConfigs(unittest.TestCase):
    """Test the _strip_agent_run_configs() function."""

    def test_removes_single_block(self):
        msg = "Hello world\n<agentRunConfig>{\"dispatch\": {\"status\": \"advance\"}}</agentRunConfig>"
        result = _strip_agent_run_configs(msg)
        self.assertEqual(result, "Hello world")
        self.assertNotIn("agentRunConfig", result)

    def test_removes_multiple_blocks(self):
        msg = ("First part\n"
                "<agentRunConfig>{\"task\": \"genius\"}</agentRunConfig>\n"
                "Middle part\n"
                "<agentRunConfig>{\"task\": \"dev\"}</agentRunConfig>\n"
                "Last part")
        result = _strip_agent_run_configs(msg)
        # Regex consumes surrounding newlines, collapsing adjacent paragraphs
        self.assertEqual(result, "First partMiddle partLast part")

    def test_noop_when_no_blocks(self):
        msg = "Just regular text without any XML blocks"
        result = _strip_agent_run_configs(msg)
        self.assertEqual(result, msg)

    def test_multiline_json_in_block(self):
        msg = ("Response text\n"
                "<agentRunConfig>\n"
                "{\n"
                "  \"dispatch\": {\n"
                "    \"status\": \"advance\",\n"
                "    \"complexity\": \"high\"\n"
                "  }\n"
                "}\n"
                "</agentRunConfig>")
        result = _strip_agent_run_configs(msg)
        self.assertEqual(result, "Response text")
        self.assertNotIn("agentRunConfig", result)

    def test_preserves_agent_metrics(self):
        msg = ("Content\n"
                "<!-- agent-metrics: {} -->\n"
                "<agentRunConfig>{\"status\": \"advance\"}</agentRunConfig>")
        result = _strip_agent_run_configs(msg)
        self.assertIn("agent-metrics", result)
        self.assertNotIn("agentRunConfig", result)


class TestIsMalformedFinalMessage(unittest.TestCase):
    """Test the is_malformed_final_message() function."""

    def test_empty_string_is_not_malformed(self):
        """Empty string is not malformed (handled by 'not final_message')."""
        self.assertFalse(is_malformed_final_message(""))

    def test_normal_text_is_not_malformed(self):
        """Plain text without any tags is not malformed."""
        self.assertFalse(is_malformed_final_message("Hello world"))

    def test_balanced_antthinking_is_not_malformed(self):
        """Balanced antThinking tags are not malformed."""
        msg = "<antThinking>some thinking</antThinking>"
        self.assertFalse(is_malformed_final_message(msg))

    def test_unclosed_antthinking_is_malformed(self):
        """Unclosed antThinking tag is malformed."""
        msg = "<antThinking>some thinking"
        self.assertTrue(is_malformed_final_message(msg))

    def test_unclosed_thinking_is_malformed(self):
        """Unclosed thinking tag is malformed."""
        msg = "<thinking>some thinking"
        self.assertTrue(is_malformed_final_message(msg))

    def test_balanced_tool_block_is_not_malformed(self):
        """Balanced tool block markers are not malformed."""
        msg = "Text before\n<tool_call>tool_call\nsome params\n</tool_call> after"
        self.assertFalse(is_malformed_final_message(msg))

    def test_unclosed_tool_block_is_malformed(self):
        """Tool block opened but not closed is malformed."""
        msg = "Some text\n<tool_call>\nfunction=terminal\nparam=value"
        self.assertTrue(is_malformed_final_message(msg))

    def test_text_with_unclosed_tool_block(self):
        """Normal text followed by mid-block truncation is malformed."""
        msg = "Here is my answer\n<tool_call>\nmore text here"
        self.assertTrue(is_malformed_final_message(msg))

    def test_multiple_balanced_blocks_are_not_malformed(self):
        """Multiple balanced blocks across different tag types are fine."""
        msg = "<antThinking>thought</antThinking> some text <tool_call>tool\n</tool_call> done"
        self.assertFalse(is_malformed_final_message(msg))

    def test_multiple_unclosed_blocks_are_malformed(self):
        """Any imbalance across multiple blocks is malformed."""
        msg = "<antThinking>thought</antThinking> <tool_call>unclosed\nsecond"
        self.assertTrue(is_malformed_final_message(msg))

    def test_only_closing_tag_is_malformed(self):
        """A closing tag without a matching opener is also malformed."""
        msg = "some text</antThinking>"
        self.assertTrue(is_malformed_final_message(msg))

    def test_balanced_tool_code_is_not_malformed(self):
        """Balanced tool_code tags are not malformed."""
        msg = "<tool_code>code here</tool_code>"
        self.assertFalse(is_malformed_final_message(msg))

    def test_unclosed_tool_code_is_malformed(self):
        """Unclosed tool_code tag is malformed."""
        msg = "<tool_code>code here"
        self.assertTrue(is_malformed_final_message(msg))


if __name__ == "__main__":
    unittest.main()
