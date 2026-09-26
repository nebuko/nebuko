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

    def test_step_prompt_file_passes_through(self):
        # Issue #551: STEP_PROMPT_FILE from resolve_workflow_routing must
        # reach the final env dict so CI can inject the step instruction.
        prompt = "Do the thing\nsecond line\n"
        wf_yaml = (
            "name: wf\n"
            "description: prompt passthrough\n"
            "trigger:\n"
            "  agents: [dev]\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: dev\n"
            "    prompt: |\n"
            "      Do the thing\n"
            "      second line\n"
            "    next: null\n"
        )
        prompt_base, workflows_dir = _make_dirs()
        with open(os.path.join(workflows_dir, "wf.yaml"), "w") as f:
            f.write(wf_yaml)
        try:
            if os.path.exists("/tmp/step_prompt.md"):
                os.unlink("/tmp/step_prompt.md")
            result = _build_result(
                {"task": "dev", "config_workflow": "wf"},
                prompt_base,
                workflows_dir,
            )
            assert result["STEP_PROMPT_FILE"] == "/tmp/step_prompt.md"
            with open("/tmp/step_prompt.md") as f:
                assert f.read() == prompt
        finally:
            if os.path.exists("/tmp/step_prompt.md"):
                os.unlink("/tmp/step_prompt.md")

    def test_delete_comments_emits_contract_all_scope(self):
        # Issue #644: the delete arm renders the DELETE_COMMENTS env contract
        # with a human-readable TASK and no agent selected.
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result(
            {"task": "", "delete_comments": {"issue": 123, "from_id": ""}},
            prompt_base,
            workflows_dir,
        )
        assert result["DELETE_COMMENTS"] == "true"
        assert result["DELETE_COMMENTS_ISSUE"] == "123"
        assert result["DELETE_COMMENTS_FROM"] == ""
        assert result["TASK"] == "Delete comments on issue 123 (all)"
        assert result["AGENT_SELECTED"] == "false"
        assert result["SKIP_AGENT"] == "true"

    def test_delete_comments_from_scope_in_task_summary(self):
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result(
            {"task": "", "delete_comments": {"issue": 123, "from_id": "5811583860"}},
            prompt_base,
            workflows_dir,
        )
        assert result["DELETE_COMMENTS_FROM"] == "5811583860"
        assert result["TASK"] == "Delete comments on issue 123 (from 5811583860)"

    def test_delete_comments_zero_from_keeps_scope_label(self):
        # An explicit "0" from_id is falsy but a real scope; it must not
        # collapse to "(all)" (issue #644 review observation).
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result(
            {"task": "", "delete_comments": {"issue": 123, "from_id": "0"}},
            prompt_base,
            workflows_dir,
        )
        assert result["DELETE_COMMENTS_FROM"] == "0"
        assert result["TASK"] == "Delete comments on issue 123 (from 0)"
