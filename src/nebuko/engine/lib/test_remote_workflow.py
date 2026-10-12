"""Tests for remote workflow support (config.py schema, remote_workflow.py, resolve integration)."""

import importlib
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from pydantic import ValidationError

from config import WorkflowStep, load_workflow


# \u2500\u2500 Schema Validation Tests \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class TestRemoteWorkflowStepSchema:
    """Test WorkflowStep schema validation for git-based workflows."""

    def test_valid_repo_with_ref(self):
        step = WorkflowStep(
            name="remote",
            repo="https://github.com/org/workflows.git",
            ref="v1.0.0",
        )
        assert step.repo == "https://github.com/org/workflows.git"
        assert step.ref == "v1.0.0"
        assert step.agent is None
        assert step.workflow is None

    def test_valid_repo_with_ref_and_workflow_name(self):
        step = WorkflowStep(
            name="remote",
            repo="https://github.com/org/workflows.git",
            ref="abc123",
            workflow_name="deploy.yaml",
        )
        assert step.workflow_name == "deploy.yaml"

    def test_repo_without_ref_raises(self):
        with pytest.raises(ValueError, match="repo.*requires.*ref"):
            WorkflowStep(name="bad", repo="https://github.com/org/workflows.git")

    def test_repo_non_https_raises(self):
        with pytest.raises(ValueError, match="HTTPS"):
            WorkflowStep(name="bad", repo="ssh://git@github.com/r.git", ref="main")

    def test_repo_file_url_raises(self):
        with pytest.raises(ValueError, match="HTTPS"):
            WorkflowStep(name="bad", repo="file:///local/path.git", ref="main")

    def test_workflow_name_with_traversal_raises(self):
        with pytest.raises(ValueError, match="workflow_name"):
            WorkflowStep(
                name="bad",
                repo="https://github.com/org/workflows.git",
                ref="main",
                workflow_name="../escape.yaml",
            )

    def test_workflow_name_absolute_path_raises(self):
        with pytest.raises(ValueError, match="workflow_name"):
            WorkflowStep(
                name="bad",
                repo="https://github.com/org/workflows.git",
                ref="main",
                workflow_name="/etc/passwd.yaml",
            )

    def test_repo_and_agent_raises(self):
        with pytest.raises(ValueError, match="both.*agent.*repo"):
            WorkflowStep(
                name="bad",
                agent="dev",
                repo="https://github.com/org/workflows.git",
                ref="main",
            )

    def test_repo_and_workflow_raises(self):
        with pytest.raises(ValueError, match="both.*workflow.*repo"):
            WorkflowStep(
                name="bad",
                workflow="review-only",
                repo="https://github.com/org/workflows.git",
                ref="main",
            )

    def test_no_source_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            WorkflowStep(name="empty")

    def test_backward_compat_workflow_and_path(self):
        step = WorkflowStep(name="sub", workflow="review-only", path="/custom/review.yaml")
        assert step.workflow == "review-only"
        assert step.path == "/custom/review.yaml"

    def test_backward_compat_agent_step(self):
        step = WorkflowStep(name="analyze", agent="architect", next="context")
        assert step.agent == "architect"
        assert step.repo is None

    def test_path_without_workflow_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            WorkflowStep(name="bad", path="/some/path.yaml")

    def test_repo_and_path_raises(self):
        with pytest.raises(ValueError, match="both.*repo.*path"):
            WorkflowStep(
                name="bad",
                repo="https://github.com/org/workflows.git",
                ref="main",
                path="/local/override.yaml",
            )


def _mock_result(returncode=0, stdout="", stderr=""):
    """Helper to create a proper mock subprocess result."""
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


# \u2500\u2500 Cache Utility Tests \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class TestCacheUtils:
    """Test remote_workflow cache utility functions."""

    @pytest.fixture(autouse=True)
    def isolated_cache(self, tmp_path):
        self._env = os.environ.get("WORKFLOW_CACHE_DIR")
        os.environ["WORKFLOW_CACHE_DIR"] = str(tmp_path / "cache")
        yield
        if self._env:
            os.environ["WORKFLOW_CACHE_DIR"] = self._env
        else:
            os.environ.pop("WORKFLOW_CACHE_DIR", None)

    def _import_module(self):
        import remote_workflow
        importlib.reload(remote_workflow)
        return remote_workflow

    def test_cache_key_deterministic(self):
        rw = self._import_module()
        key1 = rw.cache_key("https://github.com/org/repo.git", "v1.0")
        key2 = rw.cache_key("https://github.com/org/repo.git", "v1.0")
        assert key1 == key2
        assert len(key1) == 16

    def test_cache_key_different_for_different_inputs(self):
        rw = self._import_module()
        k1 = rw.cache_key("https://github.com/org/repo.git", "v1.0")
        k2 = rw.cache_key("https://github.com/org/repo.git", "v2.0")
        assert k1 != k2

    def test_cache_dir_created(self):
        rw = self._import_module()
        d = rw.cache_dir()
        assert d.is_dir()

    def test_get_cached_repo_path_none_when_missing(self):
        rw = self._import_module()
        assert rw.get_cached_repo_path("https://example.com/repo.git", "main") is None

    def test_get_cached_repo_path_returns_path_when_exists(self):
        rw = self._import_module()
        cache_path = rw.cache_dir() / rw.cache_key("https://example.com/r.git", "v1")
        cache_path.mkdir(parents=True)
        result = rw.get_cached_repo_path("https://example.com/r.git", "v1")
        assert result is not None
        assert result == str(cache_path)


# \u2500\u2500 Git Fetching Tests \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class TestFetchRemoteWorkflow:
    """Test fetch_remote_workflow with mocked subprocess calls."""

    @pytest.fixture(autouse=True)
    def isolated_cache(self, tmp_path):
        self._env = os.environ.get("WORKFLOW_CACHE_DIR")
        os.environ["WORKFLOW_CACHE_DIR"] = str(tmp_path / "cache")
        yield
        if self._env:
            os.environ["WORKFLOW_CACHE_DIR"] = self._env
        else:
            os.environ.pop("WORKFLOW_CACHE_DIR", None)

    def _import_module(self):
        import remote_workflow
        importlib.reload(remote_workflow)
        return remote_workflow

    def test_fresh_clone(self, tmp_path):
        rw = self._import_module()
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        (fake_repo / "workflows").mkdir()
        (fake_repo / "workflows" / "deploy.yaml").write_text("name: deploy")
        with patch.object(rw.subprocess, "run", return_value=_mock_result()):
            with patch.object(rw, "_find_workflow_file") as find_mock:
                find_mock.return_value = str(fake_repo / "workflows" / "deploy.yaml")
                result = rw.fetch_remote_workflow(
                    "https://github.com/org/workflows.git", "v1.0.0", "deploy.yaml"
                )
                assert result == str(fake_repo / "workflows" / "deploy.yaml")

    def test_timeout_passed_to_subprocess(self, tmp_path):
        rw = self._import_module()
        mock_run = MagicMock(return_value=_mock_result())
        with patch.object(rw.subprocess, "run", mock_run):
            with patch.object(rw, "_find_workflow_file") as find_mock:
                find_mock.return_value = str(tmp_path / "workflow.yaml")
                rw.fetch_remote_workflow(
                    "https://github.com/org/workflows.git", "main", timeout=60
                )
                call_kwargs = mock_run.call_args_list[0][1]
                assert call_kwargs["timeout"] == 60

    def test_find_workflow_root_then_workflows(self, tmp_path):
        rw = self._import_module()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "deploy.yaml").write_text("name: deploy")
        (repo_dir / "workflows").mkdir()
        path = rw._find_workflow_file(str(repo_dir), "deploy.yaml")
        assert path == str(repo_dir / "deploy.yaml")

    def test_find_workflow_workflows_subdir(self, tmp_path):
        rw = self._import_module()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "workflows").mkdir()
        (repo_dir / "workflows" / "deploy.yaml").write_text("name: deploy")
        path = rw._find_workflow_file(str(repo_dir), "deploy.yaml")
        assert path == str(repo_dir / "workflows" / "deploy.yaml")

    def test_find_default_workflow(self, tmp_path):
        rw = self._import_module()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "workflows").mkdir()
        (repo_dir / "workflows" / "default.yaml").write_text("name: default")
        path = rw._find_workflow_file(str(repo_dir), None)
        assert path == str(repo_dir / "workflows" / "default.yaml")

    def test_find_workflow_not_found_raises(self, tmp_path):
        rw = self._import_module()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="not found"):
            rw._find_workflow_file(str(repo_dir), "missing.yaml")

    def test_git_failure_raises_runtime_error(self):
        rw = self._import_module()
        with patch.object(rw.subprocess, "run", return_value=_mock_result(returncode=1, stderr="auth failed")):
            with pytest.raises(RuntimeError, match="auth failed"):
                rw.fetch_remote_workflow("https://github.com/org/workflows.git", "main")

    def test_stale_cache_removal_on_fetch_failure(self, tmp_path):
        rw = self._import_module()
        cache_path = rw.cache_dir() / rw.cache_key("https://github.com/org/r.git", "bad")
        cache_path.mkdir(parents=True)
        call_count = [0]

        def failing_git(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return _mock_result(returncode=1, stderr="error")
            return _mock_result()

        with patch.object(rw.subprocess, "run", side_effect=failing_git):
            with patch.object(rw, "_find_workflow_file") as find_mock:
                find_mock.return_value = str(tmp_path / "w.yaml")
                rw.fetch_remote_workflow("https://github.com/org/r.git", "bad")
                assert not cache_path.exists()

    def test_caching_reuses_existing_repo(self, tmp_path):
        rw = self._import_module()
        call_count = [0]

        def selective_git(*args, **kwargs):
            call_count[0] += 1
            # Simulate git clone creating the directory on first call
            if "clone" in args[0]:
                repo_dir = Path(args[0][-1])
                repo_dir.mkdir(parents=True, exist_ok=True)
            return _mock_result()

        with patch.object(rw.subprocess, "run", side_effect=selective_git):
            with patch.object(rw, "_find_workflow_file") as find_mock:
                find_mock.return_value = str(tmp_path / "w.yaml")
                rw.fetch_remote_workflow("https://github.com/org/r.git", "v1")
                rw.fetch_remote_workflow("https://github.com/org/r.git", "v1")
                # First call: clone (1 git call). Second call: fetch + checkout (2 git calls).
                assert call_count[0] == 3


# \u2500\u2500 Cache Cleanup Tests \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class TestCacheCleanup:
    """Test cleanup_cache function."""

    @pytest.fixture(autouse=True)
    def isolated_cache(self, tmp_path):
        self._env = os.environ.get("WORKFLOW_CACHE_DIR")
        os.environ["WORKFLOW_CACHE_DIR"] = str(tmp_path / "cache")
        yield
        if self._env:
            os.environ["WORKFLOW_CACHE_DIR"] = self._env
        else:
            os.environ.pop("WORKFLOW_CACHE_DIR", None)

    def _import_module(self):
        import remote_workflow
        importlib.reload(remote_workflow)
        return remote_workflow

    def test_cleanup_removes_old_entries(self, tmp_path):
        rw = self._import_module()
        cache_dir = rw.cache_dir()
        old_entry = cache_dir / "old12345678901234"
        old_entry.mkdir()
        old_time = time.time() - 7200
        os.utime(old_entry, (old_time, old_time))
        removed = rw.cleanup_cache(ttl_seconds=3600)
        assert "old12345678901234" in removed
        assert not old_entry.exists()

    def test_cleanup_keeps_recent_entries(self, tmp_path):
        rw = self._import_module()
        cache_dir = rw.cache_dir()
        recent_entry = cache_dir / "recent123456789012"
        recent_entry.mkdir()
        removed = rw.cleanup_cache(ttl_seconds=3600)
        assert "recent123456789012" not in removed
        assert recent_entry.exists()

    def test_cleanup_empty_cache(self):
        rw = self._import_module()
        removed = rw.cleanup_cache()
        assert removed == []


# \u2500\u2500 Integration: Step Loading \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class TestRemoteWorkflowStepLoading:
    """Test loading workflow definitions that contain remote repo steps."""

    def test_load_workflow_with_repo_step(self):
        raw = {
            "name": "remote-deploy",
            "description": "Deploy from remote repo",
            "steps": [{
                "name": "deploy",
                "repo": "https://github.com/org/shared-workflows.git",
                "ref": "v1.2.3",
                "workflow_name": "deploy.yaml",
            }],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(raw, f)
            f.flush()
            wf = load_workflow(f.name)
        os.unlink(f.name)
        assert wf.steps[0].repo == "https://github.com/org/shared-workflows.git"
        assert wf.steps[0].ref == "v1.2.3"
        assert wf.steps[0].workflow_name == "deploy.yaml"

    def test_load_workflow_with_repo_step_no_workflow_name(self):
        raw = {
            "name": "remote-default",
            "description": "Default from remote repo",
            "steps": [{
                "name": "run",
                "repo": "https://github.com/org/shared.git",
                "ref": "main",
            }],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(raw, f)
            f.flush()
            wf = load_workflow(f.name)
        os.unlink(f.name)
        assert wf.steps[0].workflow_name is None


# \u2500\u2500 Integration: Resolver \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class TestResolverWithRemoteWorkflows:
    """Test resolve_workflow.py handles repo-based sub-workflow steps."""

    def _import_resolver(self):
        import resolve_workflow
        importlib.reload(resolve_workflow)
        return resolve_workflow.resolve_workflow_routing

    def test_repo_step_triggers_fetch(self, tmp_path):
        resolve = self._import_resolver()
        (Path(tmp_path) / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent workflow",
            "steps": [
                {"name": "start", "agent": "architect", "next": "remote-task"},
                {
                    "name": "remote-task",
                    "repo": "https://github.com/org/workflows.git",
                    "ref": "v1.0",
                    "workflow_name": "deploy.yaml",
                    "next": "done",
                },
                {"name": "done", "agent": "dev"},
            ],
        }))
        mock_wf_content = yaml.dump({
            "name": "deploy",
            "description": "Deploy workflow",
            "steps": [{"name": "deploy-step", "agent": "dev"}],
        })
        mock_wf_path = str(tmp_path / "mock_deploy.yaml")
        Path(mock_wf_path).write_text(mock_wf_content)

        import resolve_workflow as rw_mod
        importlib.reload(rw_mod)

        with patch.object(rw_mod, "fetch_remote_workflow", return_value=mock_wf_path):
            result = resolve(
                {"task": "dev", "workflow": "parent", "step": "remote-task"},
                str(tmp_path),
                ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NAME"] == "deploy"
            assert result["WORKFLOW_STEP"] == "deploy-step"
            assert result["PARENT_WORKFLOW"] == "parent"

    def test_repo_step_fetch_failure_returns_skip(self, tmp_path):
        resolve = self._import_resolver()
        (Path(tmp_path) / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent workflow",
            "steps": [{
                "name": "remote-task",
                "repo": "https://github.com/org/workflows.git",
                "ref": "v1.0",
            }],
        }))

        import resolve_workflow as rw_mod
        importlib.reload(rw_mod)

        with patch.object(rw_mod, "fetch_remote_workflow", side_effect=RuntimeError("network error")):
            result = resolve(
                {"task": "dev", "workflow": "parent", "step": "remote-task"},
                str(tmp_path),
                ".agents/prompts",
            )
            assert result["SKIP_AGENT"] == "true"
            assert "network error" in result["REASON"]
