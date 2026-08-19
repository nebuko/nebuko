"""Tests for build_result feature (env-var dict construction)."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from resolve_config.features.build_result.build_result import _build_result  # noqa: E402
from resolve_workflow import TASK_TO_PROMPT  # noqa: E402


def _make_dirs():
    prompt_base = tempfile.mkdtemp()
    for name in TASK_TO_PROMPT.values():
        with open(os.path.join(prompt_base, name), "w") as f:
            f.write(f"# {name}")
    return prompt_base, tempfile.mkdtemp()


class TestBuildResult:
    def test_empty_task_skips(self):
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result({"task": ""}, prompt_base, workflows_dir)
        assert result == {"SKIP_AGENT": "true", "REASON": "No valid task found"}

    def test_unknown_task_skips(self):
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result({"task": "not-a-task"}, prompt_base, workflows_dir)
        assert result["SKIP_AGENT"] == "true"
        assert "Unknown task" in result["REASON"]

    def test_valid_task_builds_full_env_dict(self):
        prompt_base, workflows_dir = _make_dirs()
        config = {
            "task": "dev",
            "config_complexity": "high",
            "config_reasoning": "some reason",
            "config_model": "custom-model",
            "config_temperature": "0.7",
            "config_top_p": "0.9",
            "config_top_k": "40",
            "config_repetition_penalty": "1.1",
        }
        result = _build_result(config, prompt_base, workflows_dir)
        assert result["AGENT_SELECTED"] == "true"
        assert result["TASK"] == "dev"
        assert result["ROLE_NAME"] == "dev"
        assert result["PROMPT_LOCATION"].endswith("dev.md")
        assert result["COMPLEXITY"] == "high"
        assert result["LLM_REASONING"] == "some reason"
        assert result["CONFIG_MODEL"] == "custom-model"
        assert result["CONFIG_TEMPERATURE"] == "0.7"
        assert result["SKIP_AGENT"] == "false"

    def test_omitted_config_fields_default_to_empty(self):
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result({"task": "genius"}, prompt_base, workflows_dir)
        assert result["AGENT_ROUND"] == ""
        assert result["AGENT_SKILLS"] == ""
        assert result["CONFIG_MODEL"] == ""
        assert result["HUMAN_RESUME_INPUT"] == ""