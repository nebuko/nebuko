"""Tests for load_workflow feature."""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import (
    is_path_like_reference,
    is_valid_workflow_reference,
    load_workflow,
    resolve_workflow_reference,
)


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
        workflow_path = str(Path(__file__).parents[3] / "workflows" / "default.yaml")
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
        workflow_path = str(Path(__file__).parents[3] / "workflows" / "review-only.yaml")
        if not Path(workflow_path).exists():
            pytest.skip(f"Workflow file not found: {workflow_path}")
        wf = load_workflow(workflow_path)

        assert wf.name == "review-only"
        assert len(wf.steps) == 1
        assert wf.steps[0].agent == "review"


class TestPathBasedLoading:
    """Path-based workflow loading and reference resolution (issue #468)."""

    RAW = {
        "name": "path_wf",
        "description": "Workflow at a non-standard path",
        "trigger": {"agents": ["genius"]},
        "steps": [{"name": "step", "agent": "dev", "next": None}],
    }

    def _write(self, root: Path, rel: str) -> Path:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.dump(self.RAW))
        return target

    def test_load_workflow_non_standard_path(self, tmp_path: Path, monkeypatch):
        """load_workflow works with a file at a non-workflows/ repo-root path."""
        monkeypatch.chdir(tmp_path)
        self._write(tmp_path, "tmp_impl/test_workflow.yaml")
        wf = load_workflow("tmp_impl/test_workflow.yaml")
        assert wf.name == "path_wf"
        assert wf.steps[0].agent == "dev"

    def test_missing_file_error_message(self, tmp_path: Path, monkeypatch):
        """Missing file raises FileNotFoundError with a clear message."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match="Workflow file not found: nope/wf.yaml"):
            load_workflow("nope/wf.yaml")

    def test_is_path_like_reference(self):
        assert is_path_like_reference("workflows/default.yaml")
        assert is_path_like_reference("tmp_impl/test_workflow.yaml")
        assert is_path_like_reference("sub/dir/wf.yml")
        assert not is_path_like_reference("default")
        assert not is_path_like_reference("my-default.yaml-bak")

    def test_is_valid_workflow_reference(self):
        assert is_valid_workflow_reference("default")
        assert is_valid_workflow_reference("tmp_impl/wf.yaml")
        assert not is_valid_workflow_reference("")
        assert not is_valid_workflow_reference("/abs/path.yaml")
        assert not is_valid_workflow_reference("a/../b.yaml")

    def test_resolve_workflow_reference_name(self):
        assert resolve_workflow_reference("default", "workflows") == Path("workflows/default.yaml")

    def test_resolve_workflow_reference_workflows_prefix(self):
        assert resolve_workflow_reference("workflows/nested/wf.yaml", "wf_dir") == Path("wf_dir/nested/wf.yaml")

    def test_resolve_workflow_reference_repo_root_path(self):
        assert resolve_workflow_reference("tmp_impl/wf.yaml", "workflows") == "tmp_impl/wf.yaml"
