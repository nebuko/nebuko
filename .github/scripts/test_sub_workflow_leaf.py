"""Spec tests for sub-workflow leaf descent through a branch-mounted child.

Reproduces Anomaly A of issue #648: entering the branch-mounted child
``tmp_impl/workflow.yaml`` resolved a leaf with an EMPTY step name
(CI log: ``WORKFLOW_NAME='tmp_impl/workflow.yaml'``, ``WORKFLOW_STEP=''``,
``WORKFLOW_NEXT=''``), so the ``scaffold -> sub_impl`` hop stalled and needed
a manual ``/workflow resume``. The descent must always land on the runnable
terminal leaf and never on the mount node (issue #671).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resolve_workflow import (  # noqa: E402
    TASK_TO_PROMPT,
    SubWorkflowLeafError,
    _find_workflow_step,
    _load_child_workflow,
    _resolve_entry_leaf_for_step,
    resolve_next_from_workflow,
    resolve_workflow_routing,
    resolve_workflow_step,
)

SCAFFOLD_BRANCH = "feature/issue-671-scaffold"
CHILD_REF = "tmp_impl/workflow.yaml"

PLAN_ORCHESTRATION_YAML = """
name: plan-orchestration
description: Top-level orchestration
trigger:
  agents: [genius]
steps:
  - name: scaffold
    agent: genius
    next: sub_impl
  - name: sub_impl
    workflow: tmp_impl/workflow.yaml
    next: sub_clean
  - name: sub_clean
    agent: dev
    next: null
"""

DEV_REVIEW_LOOP_YAML = """
name: dev-review-loop
description: Reusable implement + review loop
trigger:
  agents: [dev]
steps:
  - name: implement
    agent: dev
    next: review
  - name: review
    agent: review
    next: null
"""

TMP_IMPL_YAML = """
name: tmp-impl
description: Dynamically generated per-step execution workflow
trigger:
  agents: [dev]
steps:
  - name: step_1
    workflow: dev-review-loop
    next: step_2
  - name: step_2
    workflow: dev-review-loop
    next: null
"""


def _git(args, cwd):
    subprocess.run(
        ["git", "-C", str(cwd)] + args, check=True, capture_output=True, text=True
    )


class _NestedBranchMountedFixture:
    """Origin repo whose scaffold branch mounts a nested child workflow.

    Origin layout:
      main:                       plan-orchestration.yaml + dev-review-loop.yaml
      feature/issue-671-scaffold: the above + tmp_impl/workflow.yaml

    ``worktree`` is a checkout of ``main``, so ``tmp_impl/workflow.yaml`` is
    absent there — the fetch-on-miss condition of issue #604 — and its nested
    ``step_1`` mounts ``dev-review-loop`` (present locally).
    """

    def __init__(self, tmp_path: Path):
        self.bare = tmp_path / "origin.git"
        self.seed = tmp_path / "seed"
        self.worktree = tmp_path / "worktree"
        self._build()

    def _build(self):
        seed = self.seed
        subprocess.run(
            ["git", "init", "-q", str(seed)], check=True, capture_output=True, text=True
        )
        _git(["config", "user.email", "test@example.com"], seed)
        _git(["config", "user.name", "test"], seed)
        _git(["checkout", "-q", "-b", "main"], seed)
        (seed / "dev-review-loop.yaml").write_text(DEV_REVIEW_LOOP_YAML)
        (seed / "plan-orchestration.yaml").write_text(PLAN_ORCHESTRATION_YAML)
        _git(["add", "-A"], seed)
        _git(["commit", "-q", "-m", "main"], seed)

        _git(["checkout", "-q", "-b", SCAFFOLD_BRANCH], seed)
        (seed / "tmp_impl").mkdir()
        (seed / "tmp_impl" / "workflow.yaml").write_text(TMP_IMPL_YAML)
        _git(["add", "-A"], seed)
        _git(["commit", "-q", "-m", "scaffold child"], seed)

        subprocess.run(
            ["git", "init", "-q", "--bare", str(self.bare)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(["remote", "add", "origin", str(self.bare)], seed)
        _git(["push", "-q", "origin", "main"], seed)
        _git(["push", "-q", "origin", SCAFFOLD_BRANCH], seed)

        subprocess.run(
            ["git", "clone", "-q", "-b", "main", str(self.bare), str(self.worktree)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert not (self.worktree / "tmp_impl" / "workflow.yaml").exists()


class TestBranchMountedNestedLeaf(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp_dir.name)
        self._prev_cache = os.environ.get("WORKFLOW_CACHE_DIR")
        os.environ["WORKFLOW_CACHE_DIR"] = str(self.tmp / "wf-cache")
        self.fixture = _NestedBranchMountedFixture(self.tmp)
        self.prompt_base = self.tmp / "prompts"
        self.prompt_base.mkdir()
        for name in TASK_TO_PROMPT.values():
            (self.prompt_base / name).write_text(f"# Prompt for {name}")

    def tearDown(self):
        if self._prev_cache is None:
            os.environ.pop("WORKFLOW_CACHE_DIR", None)
        else:
            os.environ["WORKFLOW_CACHE_DIR"] = self._prev_cache
        self._tmp_dir.cleanup()

    def _in_worktree(self, fn):
        prev_cwd = os.getcwd()
        try:
            os.chdir(self.fixture.worktree)
            return fn(str(self.fixture.worktree))
        finally:
            os.chdir(prev_cwd)

    def _routing(self, workflow, step):
        def run(workflows_dir):
            return resolve_workflow_routing(
                {"task": "genius", "workflow": workflow, "step": step},
                workflows_dir,
                str(self.prompt_base),
                scaffold_branch=SCAFFOLD_BRANCH,
            )

        return self._in_worktree(run)

    def _enter_sub_workflow(self, scaffold_branch):
        def run(workflows_dir):
            return resolve_next_from_workflow(
                "plan-orchestration",
                "sub_impl",
                workflows_dir,
                scaffold_branch=scaffold_branch,
            )

        return self._in_worktree(run)

    def test_entered_child_reports_leaf_workflow_not_mount_ref(self):
        """#648 defect: the mount ref was reported as the entered workflow."""
        entered = self._enter_sub_workflow(SCAFFOLD_BRANCH)

        self.assertTrue(entered.get("entered_sub_workflow"))
        self.assertEqual(entered["workflow"], "dev-review-loop")
        self.assertEqual(entered["step"], "implement")

    def test_entered_child_never_reports_empty_step(self):
        """The CI signal: ``WORKFLOW_STEP=''`` with the mount ref as name."""
        entered = self._enter_sub_workflow(SCAFFOLD_BRANCH)

        self.assertNotEqual(entered.get("step"), "")
        self.assertNotEqual(entered.get("workflow"), CHILD_REF)

    def test_unresolvable_child_is_surfaced_not_silently_skipped(self):
        """A declared ``workflow:`` mount must never be silently stepped over.

        The single pinned surface is ``SKIP_AGENT`` + a non-empty ``REASON``
        (the codebase-wide failure convention), not a fall-through to the
        mount's sibling step.
        """
        result = self._enter_sub_workflow(None)

        self.assertEqual(result.get("SKIP_AGENT"), "true")
        self.assertTrue(result.get("REASON"))
        self.assertNotIn("entered_sub_workflow", result)

    def test_unresolvable_mount_returns_typed_error(self):
        """The descent failure is a typed ``SubWorkflowLeafError``."""

        def run(workflows_dir):
            parent = _load_child_workflow("plan-orchestration", workflows_dir, None, {})
            mount_step = _find_workflow_step(parent, "sub_impl")
            return _resolve_entry_leaf_for_step(mount_step, workflows_dir, None)

        leaf = self._in_worktree(run)

        self.assertIsInstance(leaf, SubWorkflowLeafError)
        self.assertTrue(leaf.reason)

    def test_entered_child_is_routable_without_manual_resume(self):
        """The reported pair must actually resolve — no manual ``resume``."""
        entered = self._enter_sub_workflow(SCAFFOLD_BRANCH)

        followup = self._routing(entered["workflow"], entered["step"])

        self.assertEqual(followup["SKIP_AGENT"], "false")
        self.assertEqual(followup["WORKFLOW_STEP"], "implement")

    def test_entry_leaf_resolution_returns_typed_leaf(self):
        from resolve_workflow import SubWorkflowLeaf

        def run(workflows_dir):
            parent = _load_child_workflow("plan-orchestration", workflows_dir, None, {})
            mount_step = _find_workflow_step(parent, "sub_impl")
            return _resolve_entry_leaf_for_step(mount_step, workflows_dir, SCAFFOLD_BRANCH)

        leaf = self._in_worktree(run)

        self.assertIsInstance(leaf, SubWorkflowLeaf)
        self.assertEqual(leaf.agent, "dev")
        self.assertEqual(leaf.workflow, "dev-review-loop")
        self.assertEqual(leaf.step, "implement")
        self.assertNotEqual(leaf.step, "")
        self.assertEqual(
            leaf.refs[-1], {"workflow": leaf.workflow, "step": leaf.step}
        )


class TestScriptLeafResolution(unittest.TestCase):
    """The frozen ``SubWorkflowLeaf`` contract must represent script leaves.

    Regression guard: the pre-#671 dict protocol had a first-class
    ``kind="script"`` branch returning ``script_path``/``script_args``/
    ``script_timeout``; a contract that cannot express it forces the
    implement step to either drop script-leaf support or break the frozen
    signature (issue #671 review round 2).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.wfdir = Path(self._tmp.name)
        (self.wfdir / "script_parent.yaml").write_text(
            "name: script_parent\n"
            "description: Parent mounting a script child\n"
            "trigger:\n  agents: [dev]\n"
            "steps:\n"
            "  - name: run\n"
            "    workflow: script_child\n"
            "    next: null\n"
        )
        (self.wfdir / "script_child.yaml").write_text(
            "name: script_child\n"
            "description: Child whose first step is a script\n"
            "trigger:\n  agents: [dev]\n"
            "steps:\n"
            "  - name: build\n"
            "    script:\n"
            "      path: scripts/build.py\n"
            "      args: ['--flag', '1']\n"
            "      timeout_minutes: 5\n"
            "    next: null\n"
        )

    def _script_leaf(self):
        parent = _load_child_workflow("script_parent", str(self.wfdir), None, {})
        mount_step = _find_workflow_step(parent, "run")
        return _resolve_entry_leaf_for_step(mount_step, str(self.wfdir), None)

    def test_entry_leaf_represents_script_leaf(self):
        leaf = self._script_leaf()

        self.assertNotIsInstance(leaf, SubWorkflowLeafError)
        self.assertEqual(leaf.kind, "script")
        self.assertEqual(leaf.agent, "")
        self.assertEqual(leaf.step, "build")
        self.assertEqual(leaf.script_path, "scripts/build.py")
        self.assertEqual(leaf.script_args, "--flag 1")
        self.assertEqual(leaf.script_timeout, 5)
        self.assertEqual(
            leaf.refs[-1], {"workflow": leaf.workflow, "step": leaf.step}
        )

    def test_script_leaf_routes_to_run_script(self):
        result = resolve_workflow_step(
            task="dev", workflow_name="script_parent", step_name="run",
            branch_key="", workflows_dir=str(self.wfdir),
            prompt_base="/tmp/prompts",
        )

        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["RUN_SCRIPT"], "true")
        self.assertEqual(result["SCRIPT_PATH"], "scripts/build.py")
        self.assertEqual(result["SCRIPT_ARGS"], "--flag 1")
        self.assertEqual(result["SCRIPT_TIMEOUT_MINUTES"], "5")
