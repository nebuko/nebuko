"""Unit tests for the WorkflowEntryPoint value object (issue #789)."""

import sys
from pathlib import Path

from nebuko.engine.lib.config import StepPath, WorkflowDefinition, WorkflowStep  # noqa: E402
from nebuko.engine.lib.resolve_config.sub_domains.workflow_entry.workflow_entry import (  # noqa: E402
    WorkflowEntryPoint,
)


def _wf(*steps: WorkflowStep) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="wf", description="test", steps=list(steps)
    )


def _agent(name: str, agent: str) -> WorkflowStep:
    return WorkflowStep(name=name, agent=agent)


class TestWorkflowEntryPointResolve:
    def test_empty_step_resolves_first_step(self):
        wf = _wf(_agent("one", "architect"), _agent("two", "dev"))

        def resolver(step, tail=None):
            return {"task": step.agent, "config_workflow": "wf", "config_step": step.name}

        entry = WorkflowEntryPoint(workflow="wf").resolve(wf, resolver)

        assert entry.is_valid()
        assert entry.task == "architect"
        assert entry.config_workflow == "wf"
        assert entry.config_step == "one"

    def test_named_step_resolves_that_step(self):
        wf = _wf(_agent("one", "architect"), _agent("two", "dev"))
        seen = []

        def resolver(step, tail=None):
            seen.append(step.name)
            return {"task": step.agent, "config_step": step.name}

        entry = WorkflowEntryPoint(workflow="wf", step="two").resolve(wf, resolver)

        assert seen == ["two"]
        assert entry.task == "dev"
        assert entry.config_step == "two"

    def test_unknown_step_is_invalid(self):
        wf = _wf(_agent("one", "architect"))

        entry = WorkflowEntryPoint(workflow="wf", step="ghost").resolve(
            wf, lambda step, tail=None: {"task": step.agent}
        )

        assert not entry.is_valid()
        assert entry.task == ""
        assert entry.skip_reason == "step 'ghost' not found in workflow 'wf'"

    def test_empty_workflow_is_invalid(self):
        entry = WorkflowEntryPoint(workflow="wf").resolve(_wf(), lambda step, tail=None: {})

        assert not entry.is_valid()
        assert "has no steps" in entry.skip_reason

    def test_mount_step_carries_descent_overrides(self):
        wf = _wf(WorkflowStep(name="mount", workflow="child.yaml"))

        def resolver(step, tail=None):
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
            wf, lambda step, tail=None: {"skip_reason": "force-next-step: not scaffolded"}
        )

        assert not entry.is_valid()
        assert entry.skip_reason == "not scaffolded"

    def test_resolver_without_runnable_source_is_invalid(self):
        wf = _wf(_agent("one", "architect"))

        entry = WorkflowEntryPoint(workflow="wf", step="one").resolve(
            wf, lambda step, tail=None: {}
        )

        assert not entry.is_valid()
        assert "no runnable source" in entry.skip_reason

    def test_script_entry_is_valid(self):
        wf = _wf(_agent("one", "architect"))
        script = {"command": "x.sh", "args": "[]", "working_dir": "", "env": "{}", "timeout_minutes": 1}

        entry = WorkflowEntryPoint(workflow="wf", step="one").resolve(
            wf, lambda step, tail=None: {"task": "", "run_script": script}
        )

        assert entry.is_valid()
        assert entry.run_script == script


class TestWorkflowEntryPointNestedPath:
    """Dotted selectors descend into mounted sub-workflows (issue #822)."""

    def test_dotted_path_selects_head_and_carries_tail(self):
        wf = _wf(WorkflowStep(name="mount", workflow="child.yaml"))
        captured = {}

        def resolver(step, tail=None):
            captured["step"] = step.name
            captured["tail"] = tail
            return {"task": "dev", "config_workflow": "child", "config_step": "step_3"}

        entry = WorkflowEntryPoint(workflow="wf", step="mount.step_3").resolve(
            wf, resolver
        )

        assert entry.is_valid()
        assert captured["step"] == "mount"
        assert captured["tail"] == StepPath(raw="mount.step_3", segments=("step_3",))

    def test_plain_step_carries_no_tail(self):
        wf = _wf(WorkflowStep(name="mount", workflow="child.yaml"))
        captured = {}

        def resolver(step, tail=None):
            captured["tail"] = tail
            return {"task": "dev"}

        WorkflowEntryPoint(workflow="wf", step="mount").resolve(wf, resolver)

        assert captured["tail"] is None

    def test_dotted_path_over_non_mount_is_invalid(self):
        wf = _wf(_agent("one", "genius"))

        entry = WorkflowEntryPoint(workflow="wf", step="one.child").resolve(
            wf, lambda step, tail=None: {"task": step.agent}
        )

        assert not entry.is_valid()
        assert entry.task == ""

    def test_dotted_path_unknown_head_is_invalid(self):
        wf = _wf(_agent("one", "genius"))

        entry = WorkflowEntryPoint(workflow="wf", step="ghost.child").resolve(
            wf, lambda step, tail=None: {"task": step.agent}
        )

        assert not entry.is_valid()
        assert entry.skip_reason == "step 'ghost.child' not found in workflow 'wf'"

    def test_malformed_path_is_invalid(self):
        wf = _wf(WorkflowStep(name="mount", workflow="child.yaml"))

        entry = WorkflowEntryPoint(workflow="wf", step="mount..step_3").resolve(
            wf, lambda step, tail=None: {"task": "dev"}
        )

        assert not entry.is_valid()
        assert entry.task == ""
