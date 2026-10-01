"""Spec-scenario tests for the Stage B GHA cutover (issue #713).

Stage B replaces the ~30-step bash ``run-task`` job of the reusable
``openhands-multi-agent`` workflow with the single Stage A engine entry point
(``uv run python -m engine.run``):

* the workflow file is renamed to ``openhands-multi-agent.legacy.yml`` and the
  only in-repo caller (``comment-flow.yaml``) points at the new path;
* ``run-task`` collapses to the checkout + dependency-setup + one engine step;
* the reusable-workflow interface (``workflow_call`` inputs/secrets) and the two
  downstream jobs (``summarize-metrics``, ``subtask-completion-notifier``) are
  frozen — anything that consumed the monolith keeps seeing the same surface.

The frozen interface is pinned against ``fixtures/pre_cutover_contract.json``,
recorded from the monolith before the cutover, so a silent change to either the
interface or the untouched jobs fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
LEGACY_WORKFLOW = WORKFLOWS_DIR / "openhands-multi-agent.legacy.yml"
OLD_WORKFLOW = WORKFLOWS_DIR / "openhands-multi-agent.yml"
COMMENT_FLOW = WORKFLOWS_DIR / "comment-flow.yaml"
CONTRACT = json.loads((Path(__file__).parent / "fixtures" / "pre_cutover_contract.json").read_text())

ENGINE_COMMAND = "uv run python -m engine.run"
NEW_CALLER_PATH = "./.github/workflows/openhands-multi-agent.legacy.yml"

# The orchestration steps the monolith ran inline; none of them may survive the
# cutover (the engine owns that behaviour now).
REMOVED_STEP_NAMES = frozenset(
    {
        "Normalize GitHub Event Data",
        "Resolve Agent Configuration",
        "Prevent all agent-trigger loops & human ping guard",
        "Check Circuit Breaker",
        "Check Summarizer Gate",
        "Prepare Agent Query Context",
        "Run OpenHands Agent",
        "Clean and Prepare Comment",
        "Post Agent Comment to GitHub",
        "Process Split Plan (Issue Decomposition)",
    }
)

# Host wiring the engine step must carry to reach parity with the monolith's
# agent step (Stage B plan, frozen interface contract).
ENGINE_ENV_KEYS = frozenset(
    {
        "GFA_SOURCE",
        "GH_TOKEN",
        "AGENT_CONFIG_PATH",
        "LLM_API_KEY",
        "LOCAL_VLLM_API_TOKEN",
        "COMMANDCODE_API_KEY",
        "PROMPT_BODY",
    }
)


def _load(path: Path) -> dict:
    """Load a workflow file, tolerating YAML's ``on:`` -> ``True`` coercion."""
    doc = yaml.safe_load(path.read_text())
    if True in doc:  # ``on:`` parses as a boolean key
        doc["on"] = doc.pop(True)
    return doc


def _run_task_steps() -> list:
    return _load(LEGACY_WORKFLOW)["jobs"]["run-task"]["steps"]


def _step_runs(step: dict) -> str:
    return step.get("run", "")


class TestRenamedWorkflowFile:
    def test_legacy_workflow_exists_and_old_name_is_gone(self):
        assert LEGACY_WORKFLOW.is_file(), "the cutover workflow must live at the .legacy.yml path"
        assert not OLD_WORKFLOW.exists(), "the old workflow filename must not linger (no redirects)"

    def test_workflow_call_caller_points_at_the_renamed_file(self):
        caller = _load(COMMENT_FLOW)["jobs"]["agent"]["uses"]
        assert caller == NEW_CALLER_PATH


class TestCollapsedRunTask:
    def test_first_step_is_checkout(self):
        steps = _run_task_steps()
        assert steps[0].get("uses", "").startswith("actions/checkout@")

    def test_exactly_one_engine_step_and_it_is_last(self):
        steps = _run_task_steps()
        engine = [i for i, step in enumerate(steps) if ENGINE_COMMAND in _step_runs(step)]
        assert engine == [len(steps) - 1], "one engine invocation, as the final step"

    def test_dependency_setup_still_installs_the_openhands_sdk(self):
        deps = "\n".join(_step_runs(step) for step in _run_task_steps())
        assert "openhands-sdk @ git+" in deps
        assert "openhands-tools @ git+" in deps

    def test_orchestration_steps_are_gone(self):
        names = {step.get("name", "") for step in _run_task_steps()}
        assert not (names & REMOVED_STEP_NAMES), f"leftover orchestration steps: {names & REMOVED_STEP_NAMES}"


class TestFrozenInterfaceContract:
    def test_workflow_call_inputs_are_unchanged(self):
        assert _load(LEGACY_WORKFLOW)["on"]["workflow_call"]["inputs"] == CONTRACT["workflow_call"]["inputs"]

    def test_workflow_call_secrets_are_unchanged(self):
        assert _load(LEGACY_WORKFLOW)["on"]["workflow_call"]["secrets"] == CONTRACT["workflow_call"]["secrets"]

    def test_run_task_job_header_is_unchanged(self):
        job = _load(LEGACY_WORKFLOW)["jobs"]["run-task"]
        frozen = CONTRACT["run-task"]
        assert job["runs-on"] == frozen["runs-on"]
        assert job["if"] == frozen["if"]
        assert job["env"] == frozen["env"]


class TestUntouchedJobs:
    def test_summarize_metrics_is_byte_identical(self):
        assert _load(LEGACY_WORKFLOW)["jobs"]["summarize-metrics"] == CONTRACT["jobs"]["summarize-metrics"]

    def test_subtask_completion_notifier_is_byte_identical(self):
        assert (
            _load(LEGACY_WORKFLOW)["jobs"]["subtask-completion-notifier"]
            == CONTRACT["jobs"]["subtask-completion-notifier"]
        )


class TestEngineStepHostWiring:
    def test_engine_step_carries_the_frozen_host_surface(self):
        engine = _run_task_steps()[-1]
        assert _step_runs(engine).strip() == ENGINE_COMMAND
        assert ENGINE_ENV_KEYS <= set(engine.get("env", {}))
