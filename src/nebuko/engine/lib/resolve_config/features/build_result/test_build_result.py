"""Tests for build_result feature (env-var dict construction)."""

import json
import os
import sys
import tempfile
from pathlib import Path

from nebuko.engine.lib.resolve_config.features.build_result.build_result import _build_result  # noqa: E402
from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT  # noqa: E402


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
        # The no-task fallback with no recorded failure is a benign stop
        # (issue #853), so it carries the ``no_next_agent`` kind.
        assert result == {
            "SKIP_AGENT": "true",
            "REASON": "No valid task found",
            "SKIP_KIND": "no_next_agent",
        }

    def test_command_error_outranks_skip_reason(self):
        """A malformed command's usage wins and flags the notice (issue #804)."""
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result(
            {
                "task": "",
                "config_skip_reason": "/nebuko 'default': not found",
                "config_command_error": "/nebuko run workflow --name <workflow>",
            },
            prompt_base,
            workflows_dir,
        )
        assert result == {
            "SKIP_AGENT": "true",
            "REASON": "/nebuko run workflow --name <workflow>",
            "COMMAND_ERROR": "true",
        }

    def test_skip_reason_without_command_error_stays_unflagged(self):
        """The #529 workflow-name reason does not claim a command error."""
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result(
            {"task": "", "config_skip_reason": "/nebuko 'default': not found"},
            prompt_base,
            workflows_dir,
        )
        assert result == {"SKIP_AGENT": "true", "REASON": "/nebuko 'default': not found"}
        assert "COMMAND_ERROR" not in result

    def test_command_error_present_but_task_resolved_runs(self):
        """A fallback task (mention) means no skip — the error detail is moot."""
        prompt_base, workflows_dir = _make_dirs()
        result = _build_result(
            {"task": "dev", "config_command_error": "/nebuko run workflow --name <workflow>"},
            prompt_base,
            workflows_dir,
        )
        assert result["SKIP_AGENT"] == "false"
        assert "COMMAND_ERROR" not in result

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
        result = _build_result({"task": "architect"}, prompt_base, workflows_dir)
        assert result["AGENT_ROUND"] == ""
        assert result["AGENT_SKILLS"] == ""
        assert result["STEP_SKILL_SPECS"] == ""
        assert result["CONFIG_MODEL"] == ""
        assert result["HUMAN_RESUME_INPUT"] == ""

    def test_agent_skill_specs_come_from_the_roster(self, tmp_path, monkeypatch):
        """Issue #955: the resolved agent roster supplies the spec payload."""
        roster = tmp_path / "agent-config.yaml"
        roster.write_text(
            "agents:\n"
            "  dev:\n"
            "    model: ${llms.l1}\n"
            "    prompt_file: dev.md\n"
            "    skills:\n"
            "      - inject: [posting-contract]\n"
            "      - discover: [complexity]\n"
            "llms:\n"
            "  l1:\n"
            "    model_path: hosted_vllm/l1\n"
            "    provider: ${providers.p1}\n"
            "providers:\n"
            "  p1:\n"
            "    api_url: http://example.test/v1\n"
            "    api_token_env: T\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_CONFIG_PATH", str(roster))
        monkeypatch.setenv("SKILLS_DIR", str(tmp_path / "skills"))
        prompt_base, workflows_dir = _make_dirs()

        result = _build_result({"task": "dev"}, prompt_base, workflows_dir)

        assert result["AGENT_SKILL_SPECS"] == (
            '[{"mode":"inject","names":["posting-contract"]},'
            '{"mode":"discover","names":["complexity"]}]'
        )
        assert result["SKILLS_DIR"] == str(tmp_path / "skills")

    def test_an_unreadable_roster_leaves_the_spec_payload_empty(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AGENT_CONFIG_PATH", str(tmp_path / "missing.yaml"))
        prompt_base, workflows_dir = _make_dirs()

        result = _build_result({"task": "dev"}, prompt_base, workflows_dir)

        assert result["AGENT_SKILL_SPECS"] == ""
        assert result["AGENT_SELECTED"] == "true"

    def test_step_prompt_file_passes_through(self):
        # Issue #551: STEP_PROMPT_FILE from resolve_workflow_routing must
        # reach the final env dict so CI can inject the step instruction.
        prompt = "Do the thing\nsecond line\n"
        wf_yaml = (
            "name: wf\n"
            "description: prompt passthrough\n"
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


class TestConfigComplexityEmission:
    """CONFIG_COMPLEXITY wiring + per-step tier resolution (issue #787)."""

    def _tiered_dirs(self):
        prompt_base, workflows_dir = _make_dirs()
        with open(os.path.join(workflows_dir, "tiered.yaml"), "w") as f:
            f.write(
                "name: tiered\n"
                "description: tiered\n"
                "steps:\n"
                "  - name: analyze\n"
                "    agent: architect\n"
                "    complexity: low\n"
                "    next: plan\n"
                "  - name: plan\n"
                "    agent: architect\n"
                "    next: null\n"
            )
        return prompt_base, workflows_dir

    def test_step_declared_tier_reaches_config_complexity(self):
        prompt_base, workflows_dir = self._tiered_dirs()
        result = _build_result(
            {"task": "architect", "config_workflow": "tiered", "config_step": "analyze"},
            prompt_base,
            workflows_dir,
        )
        assert result["CONFIG_COMPLEXITY"] == "low"

    def test_config_complexity_is_emitted_for_architect_role(self):
        # architect/review keep COMPLEXITY="N/A" but must still carry the tier
        # through CONFIG_COMPLEXITY so the runtime override applies (#787).
        prompt_base, workflows_dir = self._tiered_dirs()
        result = _build_result(
            {"task": "architect", "config_workflow": "tiered", "config_step": "analyze"},
            prompt_base,
            workflows_dir,
        )
        assert result["COMPLEXITY"] == "N/A"
        assert result["CONFIG_COMPLEXITY"] == "low"

    def test_explicit_dispatch_tier_wins_over_step(self):
        prompt_base, workflows_dir = self._tiered_dirs()
        result = _build_result(
            {
                "task": "architect",
                "config_workflow": "tiered",
                "config_step": "analyze",
                "config_complexity": "high",
            },
            prompt_base,
            workflows_dir,
        )
        assert result["CONFIG_COMPLEXITY"] == "high"

    def test_absent_tier_defaults_to_medium(self):
        prompt_base, workflows_dir = self._tiered_dirs()
        result = _build_result(
            {"task": "architect", "config_workflow": "tiered", "config_step": "plan"},
            prompt_base,
            workflows_dir,
        )
        assert result["CONFIG_COMPLEXITY"] == "medium"

    def test_dev_role_complexity_follows_step_tier(self):
        prompt_base, workflows_dir = _make_dirs()
        with open(os.path.join(workflows_dir, "devwf.yaml"), "w") as f:
            f.write(
                "name: devwf\n"
                "description: dev tiered\n"
                "steps:\n"
                "  - name: implement\n"
                "    agent: dev\n"
                "    complexity: high\n"
                "    next: null\n"
            )
        result = _build_result(
            {"task": "dev", "config_workflow": "devwf", "config_step": "implement"},
            prompt_base,
            workflows_dir,
        )
        assert result["COMPLEXITY"] == "high"
        assert result["CONFIG_COMPLEXITY"] == "high"


class TestComplexityGovernanceOverlay:
    """The governor adjustment overlays the base tier chain (issue #869)."""

    def _low_step_dirs(self):
        prompt_base, workflows_dir = _make_dirs()
        with open(os.path.join(workflows_dir, "lowstep.yaml"), "w") as f:
            f.write(
                "name: lowstep\n"
                "description: low step\n"
                "steps:\n"
                "  - name: analyze\n"
                "    agent: architect\n"
                "    complexity: low\n"
                "    next: null\n"
            )
        return prompt_base, workflows_dir

    def _high_step_dirs(self):
        prompt_base, workflows_dir = _make_dirs()
        with open(os.path.join(workflows_dir, "highstep.yaml"), "w") as f:
            f.write(
                "name: highstep\n"
                "description: high step\n"
                "steps:\n"
                "  - name: analyze\n"
                "    agent: architect\n"
                "    complexity: high\n"
                "    next: null\n"
            )
        return prompt_base, workflows_dir

    def _adjustment(self, delta):
        from nebuko.engine.lib.complexity_governor import ComplexityAdjustment, EvidenceBundle

        return ComplexityAdjustment(
            delta=delta, trigger="evidence-drift", evidence=EvidenceBundle()
        )

    def test_adjustment_shifts_effective_tier_up(self):
        prompt_base, workflows_dir = self._low_step_dirs()
        result = _build_result(
            {
                "task": "architect",
                "config_workflow": "lowstep",
                "config_step": "analyze",
                "config_complexity_adjustment": self._adjustment(1),
                "config_complexity_policy": "{}",
            },
            prompt_base,
            workflows_dir,
        )
        assert result["CONFIG_COMPLEXITY"] == "medium"
        assert result["COMPLEXITY_ADJUSTMENT"]  # serialized snapshot emitted

    def test_no_adjustment_leaves_base_tier(self):
        prompt_base, workflows_dir = self._low_step_dirs()
        result = _build_result(
            {"task": "architect", "config_workflow": "lowstep", "config_step": "analyze"},
            prompt_base,
            workflows_dir,
        )
        assert result["CONFIG_COMPLEXITY"] == "low"
        assert result["COMPLEXITY_ADJUSTMENT"] == ""

    def test_adjustment_clamped_at_high(self):
        prompt_base, workflows_dir = self._high_step_dirs()
        result = _build_result(
            {
                "task": "architect",
                "config_workflow": "highstep",
                "config_step": "analyze",
                "config_complexity_adjustment": self._adjustment(5),
            },
            prompt_base,
            workflows_dir,
        )
        assert result["CONFIG_COMPLEXITY"] == "high"


class TestBuildResultStepHooks:
    """The resolver emits a step's session hooks as ``STEP_HOOKS`` (issue #884)."""

    _HOOKED_WF = (
        "name: wf\n"
        "description: hooks\n"
        "trigger:\n"
        "  agents: [dev]\n"
        "steps:\n"
        "  - name: implement\n"
        "    agent: dev\n"
        "    pre_hook:\n"
        "      path: scripts/init.sh\n"
        "      args: ['--full']\n"
        "      timeout_minutes: 5\n"
        "    post_hook:\n"
        "      path: scripts/stats.sh\n"
        "    next: null\n"
    )

    _PLAIN_WF = (
        "name: wf\n"
        "description: no hooks\n"
        "trigger:\n"
        "  agents: [dev]\n"
        "steps:\n"
        "  - name: implement\n"
        "    agent: dev\n"
        "    next: null\n"
    )

    def _dirs(self, yaml_text: str):
        prompt_base, workflows_dir = _make_dirs()
        with open(os.path.join(workflows_dir, "wf.yaml"), "w") as f:
            f.write(yaml_text)
        return prompt_base, workflows_dir

    def test_emits_the_declared_hooks_as_a_json_contract(self):
        prompt_base, workflows_dir = self._dirs(self._HOOKED_WF)

        result = _build_result(
            {"task": "dev", "config_workflow": "wf"},
            prompt_base,
            workflows_dir,
        )

        hooks = json.loads(result["STEP_HOOKS"])
        assert hooks["pre"] == {
            "path": "scripts/init.sh",
            "args": ["--full"],
            "timeout_minutes": 5,
        }
        assert hooks["post"] == {
            "path": "scripts/stats.sh",
            "args": None,
            "timeout_minutes": None,
        }

    def test_hookless_step_omits_the_contract(self):
        prompt_base, workflows_dir = self._dirs(self._PLAIN_WF)

        result = _build_result(
            {"task": "dev", "config_workflow": "wf"},
            prompt_base,
            workflows_dir,
        )

        assert "STEP_HOOKS" not in result

