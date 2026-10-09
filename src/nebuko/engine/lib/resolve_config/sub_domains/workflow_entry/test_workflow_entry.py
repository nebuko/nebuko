"""Unit tests for the WorkflowEntryPoint value object (issue #789)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import WorkflowDefinition, WorkflowStep  # noqa: E402
from resolve_config.sub_domains.workflow_entry.workflow_entry import (  # noqa: E402
    WorkflowEntryPoint,
)


def _wf(*steps: WorkflowStep) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="wf", description="test", trigger={"agents": ["architect"]}, steps=list(steps)
    )


def _agent(name: str, agent: str) -> WorkflowStep:
    return WorkflowStep(name=name, agent=agent)


class TestWorkflowEntryPointResolve:
    def test_empty_step_resolves_first_step(self):
        wf = _wf(_agent("one", "architect"), _agent("two", "dev"))

        def resolver(step):
            return {"task": step.agent, "config_workflow": "wf", "config_step": step.name}

        entry = WorkflowEntryPoint(workflow="wf").resolve(wf, resolver)

        assert entry.is_valid()
        assert entry.task == "architect"
        assert entry.config_workflow == "wf"
        assert entry.config_step == "one"

    def test_named_step_resolves_that_step(self):
        wf = _wf(_agent("one", "architect"), _agent("two", "dev"))
        seen = []

        def resolver(step):
            seen.append(step.name)
            return {"task": step.agent, "config_step": step.name}

        entry = WorkflowEntryPoint(workflow="wf", step="two").resolve(wf, resolver)

        assert seen == ["two"]
        assert entry.task == "dev"
        assert entry.config_step == "two"

    def test_unknown_step_is_invalid(self):
        wf = _wf(_agent("one", "architect"))

        entry = WorkflowEntryPoint(workflow="wf", step="ghost").resolve(
            wf, lambda step: {"task": step.agent}
        )

        assert not entry.is_valid()
        assert entry.task == ""
        assert entry.skip_reason == "step 'ghost' not found in workflow 'wf'"

    def test_empty_workflow_is_invalid(self):
        entry = WorkflowEntryPoint(workflow="wf").resolve(_wf(), lambda step: {})

        assert not entry.is_valid()
        assert "has no steps" in entry.skip_reason

    def test_mount_step_carries_descent_overrides(self):
        wf = _wf(WorkflowStep(name="mount", workflow="child.yaml"))

        def resolver(step):
            return {
                "task": "dev",
                "config_workflow": "child",
                "config_step": "do",
                "config_parent_workflow": "wf",
                "config_parent_workflow_step": "mount",
            }

        entry = WorkflowEntryPoint(workflow="wf", step="mount").resolve(wf, resolver)

        assert entry.is_valid()
        assert entry.config_workflow == "child"
        assert entry.config_step == "do"
        assert entry.config_parent_workflow == "wf"
        assert entry.config_parent_workflow_step == "mount"

    def test_resolver_skip_reason_strips_legacy_prefix(self):
        wf = _wf(_agent("one", "architect"))

        entry = WorkflowEntryPoint(workflow="wf", step="one").resolve(
            wf, lambda step: {"skip_reason": "force-next-step: not scaffolded"}
        )

        assert not entry.is_valid()
        assert entry.skip_reason == "not scaffolded"

    def test_resolver_without_runnable_source_is_invalid(self):
        wf = _wf(_agent("one", "architect"))

        entry = WorkflowEntryPoint(workflow="wf", step="one").resolve(
            wf, lambda step: {}
        )

        assert not entry.is_valid()
        assert "no runnable source" in entry.skip_reason

    def test_script_entry_is_valid(self):
        wf = _wf(_agent("one", "architect"))
        script = {"path": "x.sh", "args_str": "", "timeout_minutes": 1}

        entry = WorkflowEntryPoint(workflow="wf", step="one").resolve(
            wf, lambda step: {"task": "", "run_script": script}
        )

        assert entry.is_valid()
        assert entry.run_script == script
