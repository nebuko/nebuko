"""Git session workflow patch handling: detect workflow changes and deliver as patches.

The agent token has no 'workflow' scope, so GitHub rejects pushes that modify
`.github/workflows/*` files. The guard detects these, builds a patch, reverts
the files, and commits the revert so the push succeeds (issue #1024).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nebuko.engine.git_session import GitSessionGuard, PATCH_FILENAME


def _git(root, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    """A bare 'github' remote the checkout pushes to."""
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture
def checkout(tmp_path, remote):
    """A local checkout with ``origin`` pointing at the bare remote."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "remote", "add", "origin", str(remote))
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-m", "initial")
    _git(path, "push", "-u", "origin", "main")
    return path


def _local_guard(path, env=None) -> GitSessionGuard:
    """A guard whose ``origin`` is a local path (never github/HTTPS)."""
    return GitSessionGuard(path, env or {})


# ── Workflow detection and patching ──────────────────────────────


def test_finalize_detects_workflow_file_changes(checkout, remote):
    """When a workflow file is modified, finalize should handle it."""
    workflows_dir = checkout / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "ci.yaml"
    workflow_file.write_text("name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
    _git(checkout, "add", ".github/workflows/ci.yaml")
    _git(checkout, "commit", "-m", "add workflow")
    _git(checkout, "push", "origin", "main")

    # Now modify the workflow file
    workflow_file.write_text("name: CI-Updated\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n")

    result = _local_guard(checkout).finalize()

    # Should succeed despite the workflow change
    assert result.ok, result.detail
    # Workflow patch file should be created
    patch_file = checkout / ".nebuko" / PATCH_FILENAME
    assert patch_file.exists()
    patch_content = patch_file.read_text()
    assert "ci.yaml" in patch_content
    # The workflow file on remote should be the original version
    remote_content = _git(checkout, "show", f"origin/main:.github/workflows/ci.yaml")
    assert "CI-Updated" not in remote_content


def test_finalize_builds_patch_for_multiple_workflow_files(checkout, remote):
    """When multiple workflow files are modified, all should be in the patch."""
    workflows_dir = checkout / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)

    ci_file = workflows_dir / "ci.yaml"
    ci_file.write_text("name: CI\non: [push]\n")
    release_file = workflows_dir / "release.yaml"
    release_file.write_text("name: Release\non: [release]\n")

    _git(checkout, "add", ".github/workflows/")
    _git(checkout, "commit", "-m", "add workflows")
    _git(checkout, "push", "origin", "main")

    # Modify both files
    ci_file.write_text("name: CI-v2\non: [push]\n")
    release_file.write_text("name: Release-v2\non: [release]\n")

    result = _local_guard(checkout).finalize()

    assert result.ok, result.detail
    patch_file = checkout / ".nebuko" / PATCH_FILENAME
    assert patch_file.exists()
    patch_content = patch_file.read_text()
    assert "ci.yaml" in patch_content
    assert "release.yaml" in patch_content


def test_finalize_skips_patch_when_no_workflow_changes(checkout, remote):
    """When only non-workflow files are modified, no patch should be created."""
    (checkout / "regular.txt").write_text("content\n")
    _git(checkout, "add", "regular.txt")
    _git(checkout, "commit", "-m", "add regular file")
    _git(checkout, "push", "origin", "main")

    # Add another regular file
    (checkout / "another.txt").write_text("more\n")

    result = _local_guard(checkout).finalize()

    assert result.ok, result.detail
    # No patch file should be created
    patch_file = checkout / ".nebuko" / PATCH_FILENAME
    assert not patch_file.exists()


def test_finalize_reverts_workflow_files_to_remote_state(checkout, remote):
    """Workflow files should be reverted to remote state before push."""
    workflows_dir = checkout / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "ci.yaml"
    workflow_file.write_text("name: CI\non: [push]\n")
    _git(checkout, "add", ".github/workflows/ci.yaml")
    _git(checkout, "commit", "-m", "add workflow")
    _git(checkout, "push", "origin", "main")

    # Modify the workflow file locally
    workflow_file.write_text("name: CI-Modified\non: [push]\n")

    result = _local_guard(checkout).finalize()

    assert result.ok, result.detail
    # The local workflow file should be reverted to the remote version
    local_content = workflow_file.read_text()
    assert "CI-Modified" not in local_content
    assert "name: CI" in local_content


def test_finalize_commits_workflow_revert(checkout, remote):
    """A revert commit should be created for the workflow changes."""
    workflows_dir = checkout / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "ci.yaml"
    workflow_file.write_text("name: CI\non: [push]\n")
    _git(checkout, "add", ".github/workflows/ci.yaml")
    _git(checkout, "commit", "-m", "add workflow")
    _git(checkout, "push", "origin", "main")

    # Modify the workflow file
    workflow_file.write_text("name: CI-Modified\non: [push]\n")

    result = _local_guard(checkout).finalize()

    assert result.ok, result.detail
    # Check that a revert commit was created
    log = _git(checkout, "log", "--oneline", "-n", "5")
    assert "revert: remove workflow files from push" in log


def test_finalize_with_new_branch_no_remote(checkout, remote):
    """On a new branch with no remote tracking, should use origin/HEAD as base."""
    _git(checkout, "checkout", "-b", "feature/new")
    workflows_dir = checkout / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "ci.yaml"
    workflow_file.write_text("name: CI\non: [push]\n")
    _git(checkout, "add", ".github/workflows/ci.yaml")
    _git(checkout, "commit", "-m", "add workflow")

    result = _local_guard(checkout).finalize()

    # Should still succeed; patch should be created
    assert result.ok, result.detail
    patch_file = checkout / ".nebuko" / PATCH_FILENAME
    assert patch_file.exists()


def test_finalize_patch_is_valid_diff_format(checkout, remote):
    """The patch file should be in valid git diff format."""
    workflows_dir = checkout / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "ci.yaml"
    workflow_file.write_text("name: CI\non: [push]\n")
    _git(checkout, "add", ".github/workflows/ci.yaml")
    _git(checkout, "commit", "-m", "add workflow")
    _git(checkout, "push", "origin", "main")

    # Modify the workflow
    workflow_file.write_text("name: CI-v2\non: [push]\njobs:\n  test: {}\n")

    result = _local_guard(checkout).finalize()

    assert result.ok, result.detail
    patch_file = checkout / ".nebuko" / PATCH_FILENAME
    assert patch_file.exists()

    patch_content = patch_file.read_text()
    # Should contain standard diff markers
    assert "diff --git" in patch_content or "---" in patch_content


def test_finalize_mixed_workflow_and_regular_changes(checkout, remote):
    """When both workflow and regular files change, both should be handled correctly."""
    workflows_dir = checkout / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "ci.yaml"
    workflow_file.write_text("name: CI\non: [push]\n")
    _git(checkout, "add", ".github/workflows/ci.yaml")
    _git(checkout, "commit", "-m", "add workflow")
    _git(checkout, "push", "origin", "main")

    # Modify both workflow and regular files
    workflow_file.write_text("name: CI-v2\non: [push]\n")
    (checkout / "README.md").write_text("# Project\nUpdated\n")

    result = _local_guard(checkout).finalize()

    assert result.ok, result.detail
    # Patch should be created for workflow
    patch_file = checkout / ".nebuko" / PATCH_FILENAME
    assert patch_file.exists()
    # Regular file should be pushed
    remote_readme = _git(checkout, "show", "origin/main:README.md")
    assert "Updated" in remote_readme
    # Workflow should NOT be in the pushed version
    try:
        _git(checkout, "show", "origin/main:.github/workflows/ci.yaml")
        # If it exists, it should not have the v2 change
        remote_workflow = _git(checkout, "show", "origin/main:.github/workflows/ci.yaml")
        assert "CI-v2" not in remote_workflow
    except subprocess.CalledProcessError:
        # File might not exist on remote, which is also acceptable
        pass
