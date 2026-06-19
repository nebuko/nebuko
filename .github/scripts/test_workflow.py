"""Tests for workflow models (config.py) and workflow resolver (resolve_workflow.py)."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from config import (
    WorkflowCondition,
    WorkflowStep,
    WorkflowTrigger,
    WorkflowDefinition,
    load_workflow,
)


# ── Sub-workflow Schema Tests ────────────────────────────────────────


class TestWorkflowStepSubWorkflow:
    """Test WorkflowStep with sub-workflow support."""

    def test_step_with_workflow_only(self):
        step = WorkflowStep(name="subtask", workflow="review-only")
        assert step.workflow == "review-only"
        assert step.agent is None

    def test_step_with_workflow_and_path(self):
        step = WorkflowStep(name="subtask", workflow="cve", path=".custom/cve.yaml")
        assert step.workflow == "cve"
        assert step.path == ".custom/cve.yaml"

    def test_step_missing_both_raises(self):
        with pytest.raises(ValueError, match="either"):
            WorkflowStep(name="broken", next="foo")

    def test_step_has_both_raises(self):
        with pytest.raises(ValueError, match="both"):
            WorkflowStep(name="bad", agent="dev", workflow="review-only")


# ── Workflow Model Tests ─────────────────────────────────────────────


class TestWorkflowCondition:
    """Test WorkflowCondition Pydantic model."""

    def test_valid_condition(self):
        cond = WorkflowCondition(on_result="needs_review", next="review")
        assert cond.on_result == "needs_review"
        assert cond.next == "review"

    def test_condition_without_next(self):
        cond = WorkflowCondition(on_result="default")
        assert cond.on_result == "default"
        assert cond.next is None


class TestWorkflowStep:
    """Test WorkflowStep Pydantic model."""

    def test_simple_step(self):
        step = WorkflowStep(name="analyze", agent="genius", next="context")
        assert step.name == "analyze"
        assert step.agent == "genius"
        assert step.next == "context"
        assert step.conditions is None

    def test_terminal_step(self):
        step = WorkflowStep(name="review", agent="review", next=None)
        assert step.next is None

    def test_step_with_conditions(self):
        conditions = [
            WorkflowCondition(on_result="needs_review", next="review"),
            WorkflowCondition(on_result="needs_revision", next="dev"),
            WorkflowCondition(on_result="default", next=None),
        ]
        step = WorkflowStep(
            name="implement", agent="dev", conditions=conditions
        )
        assert len(step.conditions) == 3
        assert step.conditions[0].on_result == "needs_review"


class TestWorkflowTrigger:
    """Test WorkflowTrigger Pydantic model."""

    def test_valid_trigger(self):
        trigger = WorkflowTrigger(agents=["genius"])
        assert trigger.agents == ["genius"]

    def test_multiple_trigger_agents(self):
        trigger = WorkflowTrigger(agents=["genius", "review"])
        assert trigger.agents == ["genius", "review"]


class TestWorkflowDefinition:
    """Test WorkflowDefinition Pydantic model."""

    def test_valid_workflow(self):
        trigger = WorkflowTrigger(agents=["genius"])
        steps = [
            WorkflowStep(name="analyze", agent="genius", next="context"),
            WorkflowStep(name="context", agent="ctx", next=None),
        ]
        wf = WorkflowDefinition(
            name="test-workflow",
            description="A test workflow",
            trigger=trigger,
            steps=steps,
        )
        assert wf.name == "test-workflow"
        assert len(wf.steps) == 2
        assert wf.trigger.agents == ["genius"]

    def test_missing_name_raises(self):
        trigger = WorkflowTrigger(agents=["genius"])
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                description="No name",
                trigger=trigger,
                steps=[],
            )


# ── load_workflow Integration Tests ──────────────────────────────────


class TestLoadWorkflow:
    """Test the load_workflow helper function."""

    def test_load_valid_workflow(self):
        raw = {
            "name": "default",
            "description": "Standard dev workflow",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "analyze", "agent": "genius", "next": "context"},
                {"name": "context", "agent": "ctx", "next": None},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            wf = load_workflow(f.name)

        assert wf.name == "default"
        assert wf.steps[0].agent == "genius"
        assert wf.steps[1].agent == "ctx"
        os.unlink(f.name)

    def test_load_workflow_with_conditions(self):
        raw = {
            "name": "conditional",
            "description": "Workflow with conditional branching",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {
                    "name": "implement",
                    "agent": "dev",
                    "conditions": [
                        {"on_result": "needs_review", "next": "review"},
                        {"on_result": "default", "next": None},
                    ],
                },
                {"name": "review", "agent": "review", "next": None},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            wf = load_workflow(f.name)

        assert wf.steps[0].conditions is not None
        assert len(wf.steps[0].conditions) == 2
        assert wf.steps[0].conditions[0].on_result == "needs_review"
        os.unlink(f.name)

    def test_load_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_workflow("/nonexistent/workflow.yaml")

    def test_load_invalid_yaml_raises(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("invalid: yaml: [")
            f.flush()
            with pytest.raises(Exception):
                load_workflow(f.name)
            os.unlink(f.name)

    def test_load_missing_section_raises(self):
        raw = {
            "name": "incomplete",
            # Missing description, trigger, steps
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            with pytest.raises(ValidationError):
                load_workflow(f.name)
            os.unlink(f.name)

    def test_load_real_default_workflow(self):
        """Load the actual workflows/default.yaml and verify structure."""
        workflow_path = str(Path(__file__).parents[2] / "workflows" / "default.yaml")
        if not Path(workflow_path).exists():
            pytest.skip(f"Workflow file not found: {workflow_path}")
        wf = load_workflow(workflow_path)

        assert wf.name == "default"
        assert wf.trigger.agents == ["genius"]
        assert len(wf.steps) == 5
        assert wf.steps[0].name == "analyze"
        assert wf.steps[0].agent == "genius"
        assert wf.steps[0].next == "context"
        assert wf.steps[-1].name == "review"
        assert wf.steps[-1].agent == "review"
        assert wf.steps[-1].next is None

    def test_load_real_review_only_workflow(self):
        """Load the actual workflows/review-only.yaml."""
        workflow_path = str(Path(__file__).parents[2] / "workflows" / "review-only.yaml")
        if not Path(workflow_path).exists():
            pytest.skip(f"Workflow file not found: {workflow_path}")
        wf = load_workflow(workflow_path)

        assert wf.name == "review-only"
        assert len(wf.steps) == 1
        assert wf.steps[0].agent == "review"


# ── Workflow Resolver Tests ──────────────────────────────────────────


class TestResolveWorkflowRouting:
    """Test the resolve_workflow_routing function from resolve_workflow.py."""

    def _import_resolver(self):
        # Import at test time to handle path issues
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from resolve_workflow import resolve_workflow_routing
        return resolve_workflow_routing

    def test_direct_task_routing_no_workflow(self):
        """Without workflow field, resolves directly via task name."""
        resolve = self._import_resolver()
        result = resolve(
            {"task": "genius"},
            "workflows",
            ".agents/prompts",
        )
        assert result["ROLE_NAME"] == "genius"
        assert result["PROMPT_LOCATION"] == ".agents/prompts/genius.md"
        assert result["SKIP_AGENT"] == "false"
        assert result["WORKFLOW_NAME"] == ""

    def test_direct_task_routing_for_dev(self):
        """Dev task without workflow resolves directly."""
        resolve = self._import_resolver()
        result = resolve(
            {"task": "dev"},
            "workflows",
            ".agents/prompts",
        )
        assert result["ROLE_NAME"] == "dev"
        assert result["PROMPT_LOCATION"] == ".agents/prompts/dev.md"

    def test_workflow_step_resolution(self):
        """With workflow+step, resolves via workflow definition."""
        resolve = self._import_resolver()
        result = resolve(
            {"task": "ctx", "workflow": "default", "step": "context"},
            str(Path(__file__).parents[2] / "workflows"),
            ".agents/prompts",
        )
        assert result["ROLE_NAME"] == "ctx"
        assert result["WORKFLOW_NAME"] == "default"
        assert result["WORKFLOW_STEP"] == "context"
        assert result["WORKFLOW_NEXT"] == "plan"

    def test_workflow_first_step_when_step_empty(self):
        """When step is empty, defaults to first step of workflow."""
        resolve = self._import_resolver()
        result = resolve(
            {"task": "genius", "workflow": "default", "step": ""},
            str(Path(__file__).parents[2] / "workflows"),
            ".agents/prompts",
        )
        assert result["ROLE_NAME"] == "genius"
        assert result["WORKFLOW_STEP"] == "analyze"
        assert result["WORKFLOW_NEXT"] == "context"

    def test_workflow_not_found_falls_back(self):
        """If workflow file doesn't exist, falls back to direct task routing."""
        resolve = self._import_resolver()
        result = resolve(
            {"task": "review", "workflow": "nonexistent"},
            str(Path(__file__).parents[2] / "workflows"),
            ".agents/prompts",
        )
        assert result["ROLE_NAME"] == "review"
        assert result["SKIP_AGENT"] == "false"

    def test_unknown_task_skips(self):
        """Unknown task name causes skip."""
        resolve = self._import_resolver()
        result = resolve(
            {"task": "unknown_agent"},
            "workflows",
            ".agents/prompts",
        )
        assert result["SKIP_AGENT"] == "true"

    def test_empty_task_skips(self):
        """Empty task causes skip."""
        resolve = self._import_resolver()
        result = resolve(
            {},
            "workflows",
            ".agents/prompts",
        )
        assert result["SKIP_AGENT"] == "true"

    def test_unknown_step_in_workflow_returns_error(self):
        """When step name is provided but not found, returns skip with reason."""
        resolve = self._import_resolver()
        result = resolve(
            {"task": "dev", "workflow": "default", "step": "implment"},
            str(Path(__file__).parents[2] / "workflows"),
            ".agents/prompts",
        )
        assert result["SKIP_AGENT"] == "true"
        assert "implment" in result["REASON"]
        assert "not found" in result["REASON"]

    def test_conditional_routing_matching_branch(self):
        """Conditional routing with matching branch key."""
        # Create a temporary workflow with conditions
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = Path(tmpdir) / "conditional.yaml"
            wf_path.write_text(
                yaml.dump({
                    "name": "conditional",
                    "description": "Conditional workflow",
                    "trigger": {"agents": ["genius"]},
                    "steps": [
                        {
                            "name": "implement",
                            "agent": "dev",
                            "conditions": [
                                {"on_result": "needs_review", "next": "review"},
                                {"on_result": "needs_revision", "next": "dev"},
                                {"on_result": "default", "next": None},
                            ],
                        },
                        {"name": "review", "agent": "review", "next": None},
                    ],
                })
            )
            resolve = self._import_resolver()
            result = resolve(
                {"task": "dev", "workflow": "conditional", "step": "implement", "branch": "needs_review"},
                tmpdir,
                ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_STEP"] == "implement"
            assert result["WORKFLOW_NEXT"] == "review"

    def test_conditional_routing_no_match_falls_to_default(self):
        """Conditional routing with no match falls to default condition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = Path(tmpdir) / "conditional.yaml"
            wf_path.write_text(
                yaml.dump({
                    "name": "conditional",
                    "description": "Conditional workflow",
                    "trigger": {"agents": ["genius"]},
                    "steps": [
                        {
                            "name": "implement",
                            "agent": "dev",
                            "conditions": [
                                {"on_result": "needs_review", "next": "review"},
                                {"on_result": "default", "next": None},
                            ],
                        },
                        {"name": "review", "agent": "review", "next": None},
                    ],
                })
            )
            resolve = self._import_resolver()
            result = resolve(
                {"task": "dev", "workflow": "conditional", "step": "implement", "branch": "unexpected_result"},
                tmpdir,
                ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NEXT"] == ""

    def test_conditional_routing_no_conditions_on_step(self):
        """When step has no conditions, linear routing applies regardless of branch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = Path(tmpdir) / "linear.yaml"
            wf_path.write_text(
                yaml.dump({
                    "name": "linear",
                    "description": "Linear workflow",
                    "trigger": {"agents": ["genius"]},
                    "steps": [
                        {"name": "step1", "agent": "genius", "next": "step2"},
                        {"name": "step2", "agent": "dev", "next": None},
                    ],
                })
            )
            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "linear", "step": "step1", "branch": "some_branch"},
                tmpdir,
                ".agents/prompts",
            )
            # No conditions → linear routing: next is "step2"
            assert result["WORKFLOW_NEXT"] == "step2"

    def test_conditional_routing_matching_branch_with_null_next(self):
        """Conditional routing with next: null should NOT fall through to default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wf_path = Path(tmpdir) / "conditional.yaml"
            wf_path.write_text(
                yaml.dump({
                    "name": "conditional",
                    "description": "Test null next",
                    "trigger": {"agents": ["genius"]},
                    "steps": [
                        {
                            "name": "implement",
                            "agent": "dev",
                            "conditions": [
                                {"on_result": "done", "next": None},
                                {"on_result": "default", "next": "review"},
                            ],
                        },
                        {"name": "review", "agent": "review", "next": None},
                    ],
                })
            )
            resolve = self._import_resolver()
            result = resolve(
                {"task": "dev", "workflow": "conditional", "step": "implement", "branch": "done"},
                tmpdir,
                ".agents/prompts",
            )
            # branch "done" matched → next is None (explicitly terminated)
            # Must NOT fall through to default "review"
            assert result["WORKFLOW_NEXT"] == ""


# ── Sub-workflow Resolver Tests ────────────────────────────────────────


class TestSubWorkflowResolution:
    """Test sub-workflow entry, traversal, and return-to-parent."""

    def _import_resolver(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from resolve_workflow import resolve_workflow_routing
        return resolve_workflow_routing

    def test_sub_workflow_entry_to_first_step(self):
        """When a step references a workflow, resolve to its first step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create parent workflow with sub-workflow step
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "start", "agent": "genius", "next": "subtask"},
                    {
                        "name": "subtask",
                        "workflow": "child",
                        "next": "end",
                    },
                    {"name": "end", "agent": "dev", "next": None},
                ],
            }))
            # Create child workflow
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Child workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "scan", "agent": "ctx", "next": "report"},
                    {"name": "report", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "parent", "step": "subtask"},
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "ctx"
            assert result["WORKFLOW_NAME"] == "child"
            assert result["WORKFLOW_STEP"] == "scan"
            assert result["WORKFLOW_NEXT"] == "report"
            assert result["PARENT_WORKFLOW"] == "parent"
            assert result["PARENT_WORKFLOW_STEP"] == "subtask"
            assert result["SKIP_AGENT"] == "false"

    def test_sub_workflow_with_custom_path(self):
        """Sub-workflow can use path: to specify a custom location."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "custom"
            subdir.mkdir()
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {
                        "name": "subtask",
                        "workflow": "external",
                        "path": str(subdir / "custom-wf.yaml"),
                        "next": None,
                    },
                ],
            }))
            (subdir / "custom-wf.yaml").write_text(yaml.dump({
                "name": "external",
                "description": "External workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "run", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "parent", "step": "subtask"},
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NAME"] == "external"
            assert result["WORKFLOW_STEP"] == "run"
            assert result["PARENT_WORKFLOW"] == "parent"

    def test_sub_workflow_terminal_returns_to_parent(self):
        """Terminal step in sub-workflow returns control to parent's next step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "start", "agent": "genius", "next": "subtask"},
                    {"name": "subtask", "workflow": "child", "next": "finalize"},
                    {"name": "finalize", "agent": "dev", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Child workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "only-step", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {
                    "task": "review",
                    "workflow": "child",
                    "step": "only-step",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NAME"] == "parent"
            assert result["WORKFLOW_STEP"] == "finalize"
            assert result["PARENT_WORKFLOW"] == ""
            assert result["PARENT_WORKFLOW_STEP"] == ""

    def test_sub_workflow_multi_step_completion(self):
        """Multi-step sub-workflow: second step continues normally, last step returns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "subtask", "workflow": "child", "next": "after"},
                    {"name": "after", "agent": "genius", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Child workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "first", "agent": "ctx", "next": "last"},
                    {"name": "last", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            # Step 1: enter sub-workflow (already tested above)

            # Step 2: continue within child (non-terminal step)
            result = resolve(
                {
                    "task": "ctx",
                    "workflow": "child",
                    "step": "first",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "ctx"
            assert result["WORKFLOW_NEXT"] == "last"
            assert result["PARENT_WORKFLOW"] == "parent"

            # Step 3: terminal step → return to parent
            result = resolve(
                {
                    "task": "review",
                    "workflow": "child",
                    "step": "last",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "genius"
            assert result["WORKFLOW_NAME"] == "parent"
            assert result["WORKFLOW_STEP"] == "after"
            assert result["PARENT_WORKFLOW"] == ""

    def test_normal_step_preserves_empty_parent(self):
        """Normal agent step without parent context keeps parent fields empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "simple.yaml").write_text(yaml.dump({
                "name": "simple",
                "description": "Simple workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "step1", "agent": "genius", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "simple", "step": "step1"},
                tmpdir, ".agents/prompts",
            )
            assert result["PARENT_WORKFLOW"] == ""
            assert result["PARENT_WORKFLOW_STEP"] == ""

