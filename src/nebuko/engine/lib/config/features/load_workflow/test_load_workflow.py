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
            "steps": [
                {"name": "analyze", "agent": "architect", "next": "context"},
                {"name": "context", "agent": "context", "next": None},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            wf = load_workflow(f.name)

        assert wf.name == "default"
        assert wf.steps[0].agent == "architect"
        assert wf.steps[1].agent == "context"
        os.unlink(f.name)

    def test_load_workflow_with_conditions(self):
        raw = {
            "name": "conditional",
            "description": "Workflow with conditional branching",
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

    def test_load_workflow_with_step_prompt_roundtrip(self):
        """A multi-line step prompt survives YAML round-trip verbatim (issue #551)."""
        prompt_text = (
            "Analyze the transcription and provide me:\n"
            "a short summary (~5-10 sentences)\n"
            "5 Key points\n"
        )
        raw = {
            "name": "digest",
            "description": "Digest workflow with a step prompt",
            "steps": [
                {
                    "name": "analyze",
                    "agent": "architect",
                    "prompt": prompt_text,
                    "next": "distill",
                },
                {"name": "distill", "agent": "dev", "next": None},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f, default_flow_style=False)
            f.flush()
            wf = load_workflow(f.name)

        assert wf.steps[0].prompt == prompt_text
        assert wf.steps[1].prompt is None
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
            # Missing description, steps
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
        from default_paths import workflow_dirs
        workflow_path = os.path.join(workflow_dirs()[0], "default.yaml")
        if not Path(workflow_path).exists():
            pytest.skip(f"Workflow file not found: {workflow_path}")
        wf = load_workflow(workflow_path)

        assert wf.name == "default"
        assert wf.entry_agent == "architect"
        assert len(wf.steps) == 5
        assert wf.steps[0].name == "analyze"
        assert wf.steps[0].agent == "architect"
        assert wf.steps[0].next == "context"
        assert wf.steps[-1].name == "review"
        assert wf.steps[-1].agent == "review"
        assert wf.steps[-1].next is None

    def test_load_real_review_only_workflow(self):
        """Load the actual workflows/review-only.yaml."""
        from default_paths import workflow_dirs
        workflow_path = os.path.join(workflow_dirs()[0], "review-only.yaml")
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


class TestLoadWorkflowWithDirs:
    """Test ordered multi-directory resolution (issue #529)."""

    @pytest.fixture
    def tmp_dirs(self, tmp_path):
        base = tmp_path / "base"
        local = tmp_path / "local"
        for d in (base, local):
            d.mkdir()
        return base, local

    def _write(self, path: Path, raw) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(raw))
        return path

    def _wf(self, name="foo", agent="architect"):
        return {
            "name": name,
            "description": "test workflow",
            "steps": [{"name": "only", "agent": agent, "next": None}],
        }

    def test_local_wins_over_base(self, tmp_dirs):
        from config.features.load_workflow.load_workflow import load_workflow_with_dirs

        base, local = tmp_dirs
        self._write(base / "foo.yaml", self._wf(agent="dev"))
        self._write(local / "foo.yaml", self._wf(agent="context"))
        winner = load_workflow_with_dirs("foo", str(base), str(local))
        assert winner == local / "foo.yaml"

    def test_base_used_when_local_absent(self, tmp_dirs):
        from config.features.load_workflow.load_workflow import load_workflow_with_dirs

        base, local = tmp_dirs
        self._write(base / "foo.yaml", self._wf())
        winner = load_workflow_with_dirs("foo", str(base), str(local))
        assert winner == base / "foo.yaml"

    def test_invalid_local_override_fails_loudly(self, tmp_dirs):
        from config.features.load_workflow.load_workflow import load_workflow_with_dirs

        base, local = tmp_dirs
        self._write(base / "foo.yaml", self._wf())
        self._write(local / "foo.yaml", {"name": "foo"})  # missing required fields
        with pytest.raises(ValidationError):
            load_workflow_with_dirs("foo", str(base), str(local))

    def test_missing_everywhere_lists_all_searched(self, tmp_dirs):
        from config.features.load_workflow.load_workflow import load_workflow_with_dirs

        base, local = tmp_dirs
        with pytest.raises(FileNotFoundError) as excinfo:
            load_workflow_with_dirs("nope", str(base), str(local))
        msg = str(excinfo.value)
        assert str(base / "nope.yaml") in msg
        assert str(local / "nope.yaml") in msg

    def test_duplicate_dirs_scanned_once(self, tmp_dirs):
        from config.features.load_workflow.load_workflow import load_workflow_with_dirs

        base, local = tmp_dirs
        with pytest.raises(FileNotFoundError) as excinfo:
            load_workflow_with_dirs("nope", str(local), str(local))
        # Same path given twice must appear only once in the searched list.
        assert str(local / "nope.yaml") in str(excinfo.value)
        searched = str(excinfo.value).split("searched:", 1)[1]
        assert searched.count(str(local / "nope.yaml")) == 1
