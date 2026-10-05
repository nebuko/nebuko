"""Fetch-on-miss tests for branch-mounted child workflows (issue #604).

A child workflow declared via ``path:``/``workflow:`` (e.g.
``tmp_impl/workflow.yaml``) may exist only on the issue's scaffold feature
branch, not in the default-branch checkout the runner sees. These tests
exercise the full resolution path against a **real temporary local git
repository** used as the ``origin`` remote — ``fetch_remote_workflow`` is
exercised without mocks, exactly like the production runner (shallow clone
of the scaffold branch from ``git remote get-url origin``).

Covers plan Step 6:
1. Local hit wins (child present in CWD -> no fetch).
2. Miss + ``scaffold_branch`` -> child fetched from the branch, entry resolved.
3. Miss + no ``scaffold_branch`` -> clean skip, reason names the missing field.
4. Miss + ``scaffold_branch`` set but branch absent upstream -> clean skip,
   no exception leaks.
5. ``/nebuko force-step step --name <step>`` (the issue command, new grammar) threads
   the state's ``scaffold_branch`` end-to-end through ``resolve_config``.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from resolve_config.resolve_config import resolve_config
from resolve_workflow import (
    TASK_TO_PROMPT,
    _origin_repo_url,
    resolve_workflow_routing,
)

SCAFFOLD_BRANCH = "feature/issue-604-scaffold"
CHILD_REF = "tmp_impl/workflow.yaml"


def _git(args, cwd):
    subprocess.run(
        ["git", "-C", str(cwd)] + args,
        check=True,
        capture_output=True,
        text=True,
    )


class _ScaffoldFixture:
    """Bare origin repo with a branch-only child + a working checkout of main.

    Origin layout:
      main:                           parent.yaml (references tmp_impl/workflow.yaml)
      feature/issue-604-scaffold:     parent.yaml + tmp_impl/workflow.yaml

    The ``worktree`` is a checkout of ``main`` whose ``origin`` is the bare
    repo — so ``tmp_impl/workflow.yaml`` is absent there (the miss condition)
    and ``git remote get-url origin`` resolves to the bare repo.
    """

    def __init__(self, tmp_path):
        self.bare = tmp_path / "origin.git"
        self.seed = tmp_path / "seed"
        self.worktree = tmp_path / "worktree"
        self._build()

    def _build(self):
        seed = self.seed
        subprocess.run(["git", "init", "-q", str(seed)], check=True,
                       capture_output=True, text=True)
        _git(["config", "user.email", "test@example.com"], seed)
        _git(["config", "user.name", "test"], seed)
        _git(["checkout", "-q", "-b", "main"], seed)
        (seed / "parent.yaml").write_text(
            "name: parent\n"
            "description: Parent with a branch-mounted child\n"
            "trigger:\n"
            "  agents: [genius]\n"
            "steps:\n"
            "  - name: analyze\n"
            "    agent: genius\n"
            "    next: subtask\n"
            "  - name: subtask\n"
            f"    workflow: {CHILD_REF}\n"
            "    next: null\n"
        )
        _git(["add", "-A"], seed)
        _git(["commit", "-q", "-m", "parent"], seed)
        _git(["checkout", "-q", "-b", SCAFFOLD_BRANCH], seed)
        (seed / "tmp_impl").mkdir()
        (seed / "tmp_impl" / "workflow.yaml").write_text(
            "name: implchild\n"
            "description: Branch-mounted child\n"
            "trigger:\n"
            "  agents: [dev]\n"
            "steps:\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )
        _git(["add", "-A"], seed)
        _git(["commit", "-q", "-m", "scaffold child"], seed)

        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)],
                       check=True, capture_output=True, text=True)
        _git(["remote", "add", "origin", str(self.bare)], seed)
        _git(["push", "-q", "origin", "main"], seed)
        _git(["push", "-q", "origin", SCAFFOLD_BRANCH], seed)

        # Working tree: a checkout of main whose origin is the bare repo.
        # parent.yaml is present; tmp_impl/workflow.yaml is NOT (miss).
        subprocess.run(
            ["git", "clone", "-q", "-b", "main", str(self.bare), str(self.worktree)],
            check=True, capture_output=True, text=True,
        )
        assert (self.worktree / "parent.yaml").is_file()
        assert not (self.worktree / "tmp_impl" / "workflow.yaml").exists()


class _CacheIsolation:
    """Isolate the fetcher's clone cache per test (env var, no mocks)."""

    def __init__(self, tmp_path):
        self._prev = os.environ.get("WORKFLOW_CACHE_DIR")
        os.environ["WORKFLOW_CACHE_DIR"] = str(tmp_path / "wf-cache")

    def close(self):
        if self._prev is None:
            os.environ.pop("WORKFLOW_CACHE_DIR", None)
        else:
            os.environ["WORKFLOW_CACHE_DIR"] = self._prev


class TestScaffoldBranchFetchOnMiss(unittest.TestCase):
    """Fetch-on-miss resolution with a real local git origin (issue #604)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp_dir.name)
        self.cache = _CacheIsolation(self.tmp)
        self.fixture = _ScaffoldFixture(self.tmp)
        self.prompt_base = self.tmp / "prompts"
        self.prompt_base.mkdir()
        for name in TASK_TO_PROMPT.values():
            (self.prompt_base / name).write_text(f"# Prompt for {name}")

    def tearDown(self):
        self.cache.close()
        self._tmp_dir.cleanup()

    def _routing(self, agent_config, scaffold_branch):
        """resolve_workflow_routing from the checkout CWD (where origin lives)."""
        prev_cwd = os.getcwd()
        try:
            os.chdir(self.fixture.worktree)
            return resolve_workflow_routing(
                agent_config,
                str(self.fixture.worktree),
                str(self.prompt_base),
                scaffold_branch=scaffold_branch,
            )
        finally:
            os.chdir(prev_cwd)

    def test_origin_url_resolves_to_fixture_bare_repo(self):
        prev_cwd = os.getcwd()
        try:
            os.chdir(self.fixture.worktree)
            self.assertEqual(_origin_repo_url(), str(self.fixture.bare))
        finally:
            os.chdir(prev_cwd)

    def test_local_hit_wins_no_fetch(self):
        """Child present in CWD resolves locally; scaffold_branch unused."""
        local_child = self.fixture.worktree / "tmp_impl"
        local_child.mkdir()
        (local_child / "workflow.yaml").write_text(
            "name: localchild\n"
            "description: Local child\n"
            "trigger:\n"
            "  agents: [dev]\n"
            "steps:\n"
            "  - name: implement\n"
            "    agent: dev\n"
            "    next: review\n"
            "  - name: review\n"
            "    agent: review\n"
            "    next: null\n"
        )
        prev_cwd = os.getcwd()
        try:
            os.chdir(self.fixture.worktree)
            result = resolve_workflow_routing(
                {"task": "genius", "workflow": "parent", "step": "subtask"},
                str(self.fixture.worktree),
                str(self.prompt_base),
                # Deliberately bogus: must be ignored when the local hit wins.
                scaffold_branch="definitely/not-a-branch",
            )
        finally:
            os.chdir(prev_cwd)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["WORKFLOW_NAME"], "localchild")
        self.assertEqual(result["ROLE_NAME"], "dev")

    def test_miss_with_scaffold_branch_fetches_child(self):
        """Child absent locally, present on scaffold branch -> fetched entry."""
        result = self._routing(
            {"task": "genius", "workflow": "parent", "step": "subtask"},
            scaffold_branch=SCAFFOLD_BRANCH,
        )
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["ROLE_NAME"], "dev")
        self.assertEqual(result["WORKFLOW_NAME"], "implchild")
        self.assertEqual(result["WORKFLOW_STEP"], "implement")
        self.assertEqual(result["PARENT_WORKFLOW"], "parent")
        self.assertEqual(result["PARENT_WORKFLOW_STEP"], "subtask")
        self.assertEqual(
            result["PROMPT_LOCATION"],
            f"{self.prompt_base}/{TASK_TO_PROMPT['dev']}",
        )
        # The shallow clone must have landed in the isolated cache dir.
        self.assertTrue(any((self.tmp / "wf-cache").iterdir()))

    def test_miss_without_scaffold_branch_clean_skip(self):
        """No scaffold_branch in state -> clean skip, reason names the gap."""
        result = self._routing(
            {"task": "genius", "workflow": "parent", "step": "subtask"},
            scaffold_branch=None,
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("Sub-workflow not found", result["REASON"])
        self.assertIn(CHILD_REF, result["REASON"])
        self.assertIn("no scaffold_branch in state", result["REASON"])

    def test_miss_with_missing_branch_upstream_clean_skip(self):
        """Scaffold branch absent upstream -> clean skip, no exception."""
        result = self._routing(
            {"task": "genius", "workflow": "parent", "step": "subtask"},
            scaffold_branch="feature/does-not-exist",
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("Sub-workflow not found", result["REASON"])
        self.assertIn("scaffold_branch feature/does-not-exist", result["REASON"])

    def test_force_next_step_threads_scaffold_branch_from_state(self):
        """/nebuko force-step step --name subtask — the exact issue command.

        The state block carries ``scaffold_branch``; the force-next-step path
        must thread it into child resolution and fetch the branch-only child.
        """
        state = {
            "version": 1,
            "workflow": "parent",
            "step": "analyze",
            "agent": "genius",
            "scaffold_branch": SCAFFOLD_BRANCH,
        }
        body = (
            "/nebuko force-step step --name subtask\n\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            + json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->\n"
        )
        prev_cwd = os.getcwd()
        try:
            os.chdir(self.fixture.worktree)
            result = resolve_config(
                body, str(self.prompt_base), str(self.fixture.worktree),
                is_bot=False,
            )
        finally:
            os.chdir(prev_cwd)
        self.assertEqual(result["SKIP_AGENT"], "false")
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["WORKFLOW_NAME"], "implchild")
        self.assertEqual(result["WORKFLOW_STEP"], "implement")
        self.assertEqual(result["PARENT_WORKFLOW"], "parent")
        self.assertEqual(result["PARENT_WORKFLOW_STEP"], "subtask")

    def test_force_next_step_missing_branch_state_skip(self):
        """State without scaffold_branch -> clean skip via the same command."""
        state = {
            "version": 1,
            "workflow": "parent",
            "step": "analyze",
            "agent": "genius",
        }
        body = (
            "/nebuko force-step step --name subtask\n\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            + json.dumps(state)
            + "\n<!-- WORKFLOW_STATE_END -->\n"
        )
        prev_cwd = os.getcwd()
        try:
            os.chdir(self.fixture.worktree)
            result = resolve_config(
                body, str(self.prompt_base), str(self.fixture.worktree),
                is_bot=False,
            )
        finally:
            os.chdir(prev_cwd)
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("Sub-workflow not found", result["REASON"])
        self.assertIn("no scaffold_branch in state", result["REASON"])


if __name__ == "__main__":
    unittest.main()