"""Tests for resolve_next_from_workflow() in resolve_workflow.py."""

import tempfile
from pathlib import Path

import pytest
import yaml


def _import_resolver():
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from resolve_workflow import resolve_next_from_workflow
    return resolve_next_from_workflow


@pytest.fixture
def linear_workflows_dir(tmp_path: Path) -> Path:
    """Create a temporary workflows directory with a simple linear workflow."""
    (tmp_path / "linear.yaml").write_text(yaml.dump({
        "name": "linear",
        "description": "Simple linear workflow",
        "trigger": {"agents": ["genius"]},
        "steps": [
            {"name": "analyze", "agent": "genius", "next": "context"},
            {"name": "context", "agent": "ctx", "next": "implement"},
            {"name": "implement", "agent": "dev", "next": None},
        ],
    }))
    return tmp_path


@pytest.fixture
def conditional_workflows_dir(tmp_path: Path) -> Path:
    """Create a temporary workflows directory with a conditional workflow."""
    (tmp_path / "approval.yaml").write_text(yaml.dump({
        "name": "approval",
        "description": "Approval workflow with conditions",
        "trigger": {"agents": ["genius"]},
        "steps": [
            {"name": "spec", "agent": "po", "next": "approval"},
            {
                "name": "approval",
                "agent": "genius",
                "conditions": [
                    {"on_result": "approved", "next": "implement"},
                    {"on_result": "rejected", "next": "spec"},
                    {"on_result": "default", "next": "implement"},
                ],
                "next": "implement",
            },
            {"name": "implement", "agent": "dev", "next": None},
        ],
    }))
    return tmp_path


class TestLinearNextStep:
    """Test linear next step resolution."""

    def test_resolves_next_step(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "analyze", str(linear_workflows_dir),
        )
        assert result == {"task": "ctx", "step": "context"}

    def test_resolves_middle_step(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "context", str(linear_workflows_dir),
        )
        assert result == {"task": "dev", "step": "implement"}


class TestConditionalBranch:
    """Test conditional branch resolution."""

    def test_approved_branch(self, conditional_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "approval", "approval", str(conditional_workflows_dir),
            branch_result="approved",
        )
        assert result == {"task": "dev", "step": "implement", "branch": "approved"}

    def test_rejected_branch(self, conditional_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "approval", "approval", str(conditional_workflows_dir),
            branch_result="rejected",
        )
        assert result == {"task": "po", "step": "spec", "branch": "rejected"}

    def test_default_branch_on_no_match(self, conditional_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "approval", "approval", str(conditional_workflows_dir),
            branch_result="unknown_result",
        )
        assert result == {"task": "dev", "step": "implement", "branch": "default"}

    def test_no_branch_result_uses_linear_next(self, conditional_workflows_dir):
        """When branch_result is not provided, follow step.next field."""
        resolve = _import_resolver()
        result = resolve(
            "approval", "approval", str(conditional_workflows_dir),
        )
        assert result == {"task": "dev", "step": "implement"}


class TestTerminalStep:
    """Test terminal step returns None."""

    def test_terminal_step_returns_none(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "implement", str(linear_workflows_dir),
        )
        assert result is None


class TestMissingWorkflow:
    """Test missing workflow file returns None."""

    def test_missing_workflow_returns_none(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "nonexistent", "analyze", str(linear_workflows_dir),
        )
        assert result is None


class TestMissingStep:
    """Test missing step name returns None."""

    def test_missing_step_returns_none(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "ghost_step", str(linear_workflows_dir),
        )
        assert result is None


class TestAgentHintFallback:
    """Test agent_hint fallback when no workflow resolution is found."""

    def test_hint_fallback_on_missing_workflow(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "nonexistent", "analyze", str(linear_workflows_dir),
            agent_hint="dev",
        )
        assert result == {"task": "dev"}

    def test_hint_fallback_on_missing_step(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "ghost_step", str(linear_workflows_dir),
            agent_hint="genius",
        )
        assert result == {"task": "genius"}

    def test_hint_fallback_on_terminal_step(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "implement", str(linear_workflows_dir),
            agent_hint="review",
        )
        assert result == {"task": "review"}

    def test_no_hint_returns_none_on_terminal(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "implement", str(linear_workflows_dir),
        )
        assert result is None
