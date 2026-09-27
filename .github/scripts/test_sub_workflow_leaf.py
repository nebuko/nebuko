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
import tempfile
import unittest
from pathlib import Path

from resolve_workflow import (
    TASK_TO_PROMPT,
    _find_workflow_step,
    _load_child_workflow,
    resolve_next_from_workflow,
    resolve_workflow_routing,
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
        """A declared ``workflow:`` mount must never be silently stepped over."""
        result = self._enter_sub_workflow(None)

        self.assertTrue(
            result.get("entered_sub_workflow")
            or result.get("error")
            or result.get("SKIP_AGENT") == "true",
            f"mount silently skipped: {result}",
        )

    def test_entered_child_is_routable_without_manual_resume(self):
        """The reported pair must actually resolve — no manual ``resume``."""
        entered = self._enter_sub_workflow(SCAFFOLD_BRANCH)

        followup = self._routing(entered["workflow"], entered["step"])

        self.assertEqual(followup["SKIP_AGENT"], "false")
        self.assertEqual(followup["WORKFLOW_STEP"], "implement")

    def test_entry_leaf_resolution_returns_typed_leaf(self):
        from resolve_workflow import SubWorkflowLeaf, _resolve_entry_leaf_for_step

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
