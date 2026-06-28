"""Tests for agent_run_config_to_dispatch converter."""

import json
import unittest
from build_output import agent_run_config_to_dispatch


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


if __name__ == "__main__":
    unittest.main()
