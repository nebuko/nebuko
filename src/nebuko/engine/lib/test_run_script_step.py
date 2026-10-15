"""Seam tests for the deterministic ``script`` step (issue #541).

Covers the resolver->env-var contract that GHA branches on:

* ``resolve_next_from_workflow`` returns a ``RouteResolution`` of kind
  ``RUN_SCRIPT`` (not a plain route) when the successor step is a script step.
* ``_build_result`` maps that payload to the ``RUN_SCRIPT``/``SCRIPT_PATH``/
  ... environment contract and skips agent routing entirely.
* Full ``resolve_config`` (dispatch-driven advance) reaches the executor
  contract end-to-end — the load-bearing integration point.
"""

import os
import tempfile

from resolve_config import resolve_config
from resolve_config.features.build_result.build_result import _build_result
from resolve_workflow import (
    TASK_TO_PROMPT,
    RouteKind,
    resolve_next_from_workflow,
)


def _make_dirs(workflow_yaml: str):
    prompt_base = tempfile.mkdtemp()
    workflows_dir = tempfile.mkdtemp()
    for name in TASK_TO_PROMPT.values():
        with open(os.path.join(prompt_base, name), "w") as f:
            f.write(f"# Prompt for {name}")
    if workflow_yaml:
        with open(os.path.join(workflows_dir, "default.yaml"), "w") as f:
            f.write(workflow_yaml)
    return prompt_base, workflows_dir


class TestResolveNextScriptMarker:
    """resolve_next_from_workflow returns a run_script marker for script successors."""

    SCRIPT_WF = (
        "name: default\n"
        "description: test\n"
        "steps:\n"
        "  - name: analyze\n"
        "    agent: architect\n"
        "    next: run-tests\n"
        "  - name: run-tests\n"
        "    script:\n"
        "      command: scripts/test.sh\n"
        "      args: ['--unit']\n"
        "      timeout_minutes: 15\n"
        "    next: review\n"
        "  - name: review\n"
        "    agent: review\n"
        "    next: null\n"
    )

    def test_script_successor_returns_marker(self):
        _, workflows_dir = _make_dirs(self.SCRIPT_WF)
        result = resolve_next_from_workflow("default", "analyze", workflows_dir)
        assert result.kind is RouteKind.RUN_SCRIPT
        assert result.task == ""
        assert result.step == "run-tests"
        assert result.script["path"] == "scripts/test.sh"
        assert result.script["args_str"] == "--unit"
        assert result.script["timeout_minutes"] == 15

    def test_agent_successor_unaffected(self):
        _, workflows_dir = _make_dirs(self.SCRIPT_WF)
        result = resolve_next_from_workflow("default", "run-tests", workflows_dir)
        assert result.kind is RouteKind.ROUTE
        assert result.task == "review"
        assert result.step == "review"
        assert result.script is None

    def test_script_minimal_fields(self):
        yaml = (
            "name: default\n"
            "description: test\n"
            "steps:\n"
            "  - name: a\n"
            "    agent: architect\n"
            "    next: s\n"
            "  - name: s\n"
            "    script:\n"
            "      command: ci/run.sh\n"
            "    next: b\n"
            "  - name: b\n"
            "    agent: review\n"
            "    next: null\n"
        )
        _, workflows_dir = _make_dirs(yaml)
        result = resolve_next_from_workflow("default", "a", workflows_dir)
        assert result.kind is RouteKind.RUN_SCRIPT
        assert result.script["path"] == "ci/run.sh"
        assert result.script["args_str"] == ""
        assert result.script["timeout_minutes"] is None


class TestBuildResultRunScript:
    """_build_result emits the executor env contract for a run_script config."""

    def _config(self):
        return {
            "run_script": {
                "command": "scripts/test.sh",
                "args": '["--unit", "--fast"]',
                "working_dir": "",
                "env": "{}",
                "timeout_minutes": 20,
            },
            "config_workflow": "default",
            "config_step": "run-tests",
            "config_branch": "",
            "config_parent_workflow": "",
            "config_parent_workflow_step": "",
        }

    def test_env_contract(self):
        prompt_base, workflows_dir = _make_dirs("")
        result = _build_result(self._config(), prompt_base, workflows_dir)
        assert result["RUN_SCRIPT"] == "true"
        assert result["SKIP_AGENT"] == "true"
        assert result["AGENT_SELECTED"] == "false"
        assert result["ROLE_NAME"] == "script"
        assert result["TASK"] == ""
        assert result["SCRIPT_PATH"] == "scripts/test.sh"
        assert result["SCRIPT_ARGS"] == '["--unit", "--fast"]'
        assert result["SCRIPT_TIMEOUT_MINUTES"] == "20"
        assert result["WORKFLOW_STEP"] == "run-tests"
        assert "REASON" not in result

    def test_workflow_step_plumbs_into_the_deterministic_step(self):
        """WORKFLOW_STEP becomes the step name a script comment is tagged with."""
        prompt_base, workflows_dir = _make_dirs("")
        result = _build_result(self._config(), prompt_base, workflows_dir)

        from nebuko.engine.models import DeterministicStep

        step = DeterministicStep.from_resolved(result)
        assert step is not None
        assert step.kind == "run_script"
        assert step.step_name == "run-tests"

    def test_empty_timeout_defaults_blank(self):
        cfg = self._config()
        cfg["run_script"]["timeout_minutes"] = None
        cfg["run_script"]["args"] = "[]"
        prompt_base, workflows_dir = _make_dirs("")
        result = _build_result(cfg, prompt_base, workflows_dir)
        assert result["SCRIPT_TIMEOUT_MINUTES"] == ""
        assert result["SCRIPT_ARGS"] == "[]"

    def test_agent_task_still_routed(self):
        # Regression guard: a plain agent task must NOT pick up RUN_SCRIPT.
        yaml = (
            "name: default\n"
            "description: t\n"
            "steps:\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: null\n"
        )
        prompt_base, workflows_dir = _make_dirs(yaml)
        cfg = {
            "task": "dev",
            "config_workflow": "default",
            "config_step": "implement",
            "config_complexity": "high",
            "config_reasoning": "none",
            "config_model": "",
            "config_temperature": "",
            "config_round": "",
            "config_skills": "",
            "config_top_p": "",
            "config_top_k": "",
            "config_repetition_penalty": "",
            "config_branch": "",
            "config_parent_workflow": "",
            "config_parent_workflow_step": "",
        }
        result = _build_result(cfg, prompt_base, workflows_dir)
        assert result["ROLE_NAME"] == "dev"
        assert "RUN_SCRIPT" not in result
        assert result.get("AGENT_SELECTED") == "true"


class TestResolveConfigEndToEnd:
    """Full resolve_config reaches the executor contract for a script successor."""

    SCRIPT_WF = (
        "name: default\n"
        "description: test\n"
        "steps:\n"
        "  - name: analyze\n"
        "    agent: architect\n"
        "    next: run-tests\n"
        "  - name: run-tests\n"
        "    script:\n"
        "      command: scripts/test.sh\n"
        "      args: ['--unit']\n"
        "    next: review\n"
        "  - name: review\n"
        "    agent: review\n"
        "    next: null\n"
    )

    def test_bot_comment_advancing_into_script(self):
        """A bot comment whose recorded outcome advances into the script step."""
        prompt_base, workflows_dir = _make_dirs(self.SCRIPT_WF)
        body = (
            "<!-- bot-workflow-terminal -->\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            '{"workflow": "default", "step": "analyze", '
            '"turn_outcome": {"status": "completed", "appreciation": "approved"}}\n'
            "<!-- WORKFLOW_STATE_END -->\n"
        )
        result = resolve_config(body, prompt_base, workflows_dir)
        assert result["RUN_SCRIPT"] == "true"
        assert result["SCRIPT_PATH"] == "scripts/test.sh"
        assert result["SCRIPT_ARGS"] == '["--unit"]'
        assert result["ROLE_NAME"] == "script"
        assert result["SKIP_AGENT"] == "true"
