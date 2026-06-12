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
