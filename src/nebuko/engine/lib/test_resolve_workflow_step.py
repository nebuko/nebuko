"""Tests for resolve_next_from_workflow() in resolve_workflow.py."""

import sys
import tempfile
from pathlib import Path

import pytest
import yaml


from nebuko.engine.lib.resolve_workflow import RouteKind  # noqa: E402


def _import_resolver():
    from nebuko.engine.lib.resolve_workflow import resolve_next_from_workflow

    # The resolver returns a typed ``RouteResolution`` (issues #923, #964);
    # these assertions consume it directly.
    return resolve_next_from_workflow


@pytest.fixture
def linear_workflows_dir(tmp_path: Path) -> Path:
    """Create a temporary workflows directory with a simple linear workflow."""
    (tmp_path / "linear.yaml").write_text(yaml.dump({
        "name": "linear",
        "description": "Simple linear workflow",
        "steps": [
            {"name": "analyze", "agent": "architect", "next": "context"},
            {"name": "context", "agent": "context", "next": "implement"},
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
        "steps": [
            {"name": "spec", "agent": "po", "next": "approval"},
            {
                "name": "approval",
                "agent": "architect",
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
        assert (result.task, result.step) == ("context", "context")

    def test_resolves_middle_step(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "context", str(linear_workflows_dir),
        )
        assert result.kind is RouteKind.ROUTE
        assert (result.task, result.step) == ("dev", "implement")


class TestConditionalBranch:
    """Test conditional branch resolution."""

    def test_approved_branch(self, conditional_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "approval", "approval", str(conditional_workflows_dir),
            branch_result="approved",
        )
        assert result.kind is RouteKind.ROUTE
        assert (result.task, result.step, result.branch) == ("dev", "implement", "approved")

    def test_rejected_branch(self, conditional_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "approval", "approval", str(conditional_workflows_dir),
            branch_result="rejected",
        )
        assert result.kind is RouteKind.ROUTE
        assert (result.task, result.step, result.branch) == ("po", "spec", "rejected")

    def test_default_branch_on_no_match(self, conditional_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "approval", "approval", str(conditional_workflows_dir),
            branch_result="unknown_result",
        )
        assert result.kind is RouteKind.ROUTE
        assert (result.task, result.step, result.branch) == ("dev", "implement", "default")

    def test_no_branch_result_uses_linear_next(self, conditional_workflows_dir):
        """When branch_result is not provided, follow step.next field."""
        resolve = _import_resolver()
        result = resolve(
            "approval", "approval", str(conditional_workflows_dir),
        )
        assert result.kind is RouteKind.ROUTE
        assert (result.task, result.step) == ("dev", "implement")


class TestTerminalStep:
    """Test terminal step returns None."""

    def test_terminal_step_returns_none(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "implement", str(linear_workflows_dir),
        )
        assert result.kind is RouteKind.DECLARED_FINISH


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
        assert result.task == "dev"

    def test_hint_fallback_on_missing_step(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "ghost_step", str(linear_workflows_dir),
            agent_hint="architect",
        )
        assert result.task == "architect"

    def test_hint_fallback_on_terminal_step(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "implement", str(linear_workflows_dir),
            agent_hint="review",
        )
        assert result.task == "review"

    def test_no_hint_returns_none_on_terminal(self, linear_workflows_dir):
        resolve = _import_resolver()
        result = resolve(
            "linear", "implement", str(linear_workflows_dir),
        )
        assert result.kind is RouteKind.DECLARED_FINISH
    """Test sub-workflow entry scenarios."""

    @pytest.fixture
    def sub_wf_workflows_dir(self, tmp_path: Path) -> Path:
        """Create workflows where parent has a step referencing a child workflow."""
        # Parent workflow — step "subtask" references child workflow
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent with sub-workflow",
            "steps": [
                {"name": "start", "agent": "architect", "next": "subtask"},
                {
                    "name": "subtask",
                    "workflow": "child",
                    "next": "finish",
                },
                {"name": "finish", "agent": "review", "next": None},
            ],
        }))
        # Child workflow
        (tmp_path / "child.yaml").write_text(yaml.dump({
            "name": "child",
            "description": "Child sub-workflow",
            "steps": [
                {"name": "child_start", "agent": "dev", "next": "child_end"},
                {"name": "child_end", "agent": "review", "next": None},
            ],
        }))
        return tmp_path

    def test_sub_workflow_entry_resolves_first_step(self, sub_wf_workflows_dir):
        """A sub-workflow reference step yields an entry marker for the child's first step."""
        resolve = _import_resolver()
        result = resolve(
            "parent", "subtask", str(sub_wf_workflows_dir),
        )
        assert result.kind is RouteKind.SUB_WORKFLOW_ENTER
        assert (result.task, result.step) == ("dev", "child_start")
        assert (result.workflow, result.parent_workflow, result.parent_workflow_step) == (
            "child", "parent", "subtask"
        )
    def test_sub_workflow_first_step_resolution(self, sub_wf_workflows_dir):
        """Resolving from the first step of a child workflow works normally."""
        resolve = _import_resolver()
        result = resolve(
            "child", "child_start", str(sub_wf_workflows_dir),
        )
        assert (result.task, result.step) == ("review", "child_end")


class TestSubWorkflowTraversal:
    """Integration tests for full sub-workflow entry/exit traversal."""

    @pytest.fixture
    def nested_dirs(self, tmp_path: Path) -> tuple[Path, Path]:
        """Parent + child workflows used for end-to-end traversal."""
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent with sub-workflow",
            "steps": [
                {"name": "analyze", "agent": "architect", "next": "subtask"},
                {
                    "name": "subtask",
                    "workflow": "child",
                    "next": "finish",
                },
                {"name": "finish", "agent": "review", "next": None},
            ],
        }))
        (tmp_path / "child.yaml").write_text(yaml.dump({
            "name": "child",
            "description": "Child sub-workflow",
            "steps": [
                {"name": "impl", "agent": "dev", "next": "verify"},
                {"name": "verify", "agent": "review", "next": None},
            ],
        }))
        return tmp_path, tmp_path / "workflows"

    def test_full_traversal_entry_to_exit(self, nested_dirs):
        """parent.analyze → enter sub → child.impl → child.verify (terminal)
        → parent.finish (step after the sub reference)."""
        wf_dir, _ = nested_dirs
        resolve = _import_resolver()

        # 1. Parent's first step advances to the sub-workflow reference and
        #    descends into the child's first runnable step (issue #657): a
        #    linear target that is a mount must not yield an empty task.
        entered = resolve("parent", "analyze", str(wf_dir))
        assert entered.kind is RouteKind.SUB_WORKFLOW_ENTER
        assert (entered.task, entered.step) == ("dev", "impl")
        assert (entered.workflow, entered.parent_workflow, entered.parent_workflow_step) == (
            "child", "parent", "subtask"
        )
        # 2. Sub-workflow reference step yields an entry marker
        entry = resolve("parent", "subtask", str(wf_dir))
        assert entry.kind is RouteKind.SUB_WORKFLOW_ENTER
        assert (entry.task, entry.step) == ("dev", "impl")
        assert (entry.workflow, entry.parent_workflow, entry.parent_workflow_step) == (
            "child", "parent", "subtask"
        )
        # 3. Inside the child: normal linear resolution
        r = resolve(
            "child", "impl", str(wf_dir),
            parent_workflow="parent", parent_workflow_step="subtask",
        )
        assert (r.task, r.step) == ("review", "verify")

        # 4. Child terminal step exits back to the parent's next step
        r = resolve(
            "child", "verify", str(wf_dir),
            parent_workflow="parent", parent_workflow_step="subtask",
        )
        assert r.kind is RouteKind.SUB_WORKFLOW_EXIT
        assert (r.task, r.step, r.workflow) == ("review", "finish", "parent")

        # 5. Parent terminal (no sub-workflow context): overall terminal
        r = resolve("parent", "finish", str(wf_dir))
        assert r.kind is RouteKind.DECLARED_FINISH

    def test_child_loop_stays_within_sub_workflow(self, tmp_path: Path):
        """A rejected-review loop inside the child never escapes the child."""
        (tmp_path / "loop_parent.yaml").write_text(yaml.dump({
            "name": "loop_parent",
            "description": "Parent of looping child",
            "steps": [
                {"name": "start", "agent": "architect", "next": "run"},
                {"name": "run", "workflow": "loop_child", "next": "done"},
                {"name": "done", "agent": "review", "next": None},
            ],
        }))
        (tmp_path / "loop_child.yaml").write_text(yaml.dump({
            "name": "loop_child",
            "description": "Child with a rejection loop",
            "steps": [
                {"name": "implement", "agent": "dev", "next": "review"},
                {
                    "name": "review",
                    "agent": "review",
                    "conditions": [
                        {"on_result": "approved", "next": None},
                        {"on_result": "rejected", "next": "implement"},
                        {"on_result": "default", "next": "implement"},
                    ],
                },
            ],
        }))
        resolve = _import_resolver()

        parent_ctx = dict(parent_workflow="loop_parent", parent_workflow_step="run")

        # implement → review
        r = resolve("loop_child", "implement", str(tmp_path), **parent_ctx)
        assert (r.task, r.step) == ("review", "review")
        # rejected → loops back inside the child (no parent escape)
        r = resolve(
            "loop_child", "review", str(tmp_path), branch_result="rejected", **parent_ctx,
        )
        assert (r.task, r.step, r.branch) == ("dev", "implement", "rejected")
        # approved → child terminal → exit to parent's step after the ref
        exit_r = resolve(
            "loop_child", "review", str(tmp_path), branch_result="approved", **parent_ctx,
        )
        assert exit_r.kind is RouteKind.SUB_WORKFLOW_EXIT
        assert (exit_r.task, exit_r.step, exit_r.workflow) == (
            "review", "done", "loop_parent"
        )

    def test_terminal_without_parent_returns_none(self, nested_dirs):
        """Child terminal with no parent context is overall terminal."""
        wf_dir, _ = nested_dirs
        resolve = _import_resolver()
        assert resolve("child", "verify", str(wf_dir)).kind is RouteKind.DECLARED_FINISH

    def test_path_based_workflow_reference(self, tmp_path: Path, monkeypatch):
        """resolve_next_from_workflow accepts a repo-root-relative path."""
        monkeypatch.chdir(tmp_path)
        subdir = tmp_path / "tmp_impl"
        subdir.mkdir()
        (subdir / "flow.yaml").write_text(yaml.dump({
            "name": "flow",
            "description": "Path-resolved workflow",
            "steps": [
                {"name": "one", "agent": "dev", "next": "two"},
                {"name": "two", "agent": "review", "next": None},
            ],
        }))
        resolve = _import_resolver()
        path_result = resolve("tmp_impl/flow.yaml", "one", str(tmp_path))
        assert (path_result.task, path_result.step) == ("review", "two")


class TestThreeLevelNestingRuntime:
    """Runtime-path (resolve_workflow_step) three-level nesting: the outer
    parent pair must survive a nested re-entry so the chain unwinds one
    level per return (issue #609, plan-orchestration -> tmp-impl ->
    dev-review-loop shape)."""

    @pytest.fixture
    def three_level_dir(self, tmp_path: Path) -> Path:
        # parent.mount -> workflow: mid (terminal mount)
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Outer parent",
            "steps": [
                {"name": "analyze", "agent": "architect", "next": "mount"},
                {"name": "mount", "workflow": "mid", "next": None},
                {"name": "done", "agent": "review", "next": None},
            ],
        }))
        # mid.run_1 -> workflow: leaf, next run_2; mid.run_2 -> workflow: leaf, terminal
        (tmp_path / "mid.yaml").write_text(yaml.dump({
            "name": "mid",
            "description": "Intermediate loop driver",
            "steps": [
                {"name": "run_1", "workflow": "leaf", "next": "run_2"},
                {"name": "run_2", "workflow": "leaf", "next": None},
            ],
        }))
        # leaf: dev-review-loop shape
        (tmp_path / "leaf.yaml").write_text(yaml.dump({
            "name": "leaf",
            "description": "Inner loop",
            "steps": [
                {"name": "implement", "agent": "dev", "next": "review"},
                {"name": "review", "agent": "review", "next": None},
            ],
        }))
        return tmp_path

    def _r(self, tmp_path):
        import sys
        from nebuko.engine.lib.resolve_workflow import resolve_workflow_step
        return lambda task, workflow, step, **kw: resolve_workflow_step(
            task=task, workflow_name=workflow, step_name=step,
            workflows_dir=str(tmp_path), prompt_base="/tmp/prompts", **kw,
        )

    def test_outer_parent_survives_nested_reentry(self, three_level_dir):
        """Entering mid.run_1 from inside the parent records the outer parent
        pair; completing the first leaf loop re-enters run_2 while the state's
        PARENT_WORKFLOW still records the outer parent (not clobbered)."""
        r = self._r(three_level_dir)

        # Entering mid.run_1 with the outer (parent, mount) pair on record
        # descends to the leaf; the entry runs in the leaf workflow while the
        # outer parent pair must survive.
        entry = r(
            task="dev", workflow="mid", step="run_1", branch_key="",
            parent_workflow="parent", parent_workflow_step="mount",
        )
        assert entry["ROLE_NAME"] == "dev"
        assert entry["WORKFLOW_STEP"] == "implement"
        assert entry["WORKFLOW_NAME"] == "leaf"
        assert entry["PARENT_WORKFLOW"] == "parent"
        assert entry["PARENT_WORKFLOW_STEP"] == "mount"

        # Complete the first leaf: leaf.review is terminal -> return to
        # mid.run_1's next -> re-enter run_2 (another leaf descent) and the
        # outer (parent, mount) pair must STILL be on record.
        ret = r(
            task="review", workflow="leaf", step="review", branch_key="",
            parent_workflow="mid", parent_workflow_step="run_1",
        )
        assert ret["ROLE_NAME"] == "dev"
        assert ret["WORKFLOW_STEP"] == "implement"
        assert ret["WORKFLOW_NAME"] == "leaf"
        assert ret["PARENT_WORKFLOW"] == "parent"
        assert ret["PARENT_WORKFLOW_STEP"] == "mount"

        # Complete the second (terminal) leaf: mid is terminal, so the chain
        # unwinds one more level to the outer parent: parent.resume (next
        # after mount is None -> terminal), so with no further outer pair the
        # run ends cleanly (Workflow completed) instead of losing the parent.
        end = r(
            task="review", workflow="leaf", step="review", branch_key="",
            parent_workflow="mid", parent_workflow_step="run_2",
        )
        assert end["SKIP_AGENT"] == "true"
        assert end["REASON"] == "Workflow completed"

    def test_three_level_returns_to_outer_after_mid_terminates(self, three_level_dir):
        """With a non-terminal mount (mid.mount next: done), completing the
        last leaf unwinds to mid's terminal and then back to the outer
        parent's step after the mount."""
        # Rewrite parent so mount has a follow-up step.
        (three_level_dir / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Outer parent",
            "steps": [
                {"name": "analyze", "agent": "architect", "next": "mount"},
                {"name": "mount", "workflow": "mid", "next": "done"},
                {"name": "done", "agent": "review", "next": None},
            ],
        }))
        r = self._r(three_level_dir)
        end = r(
            task="review", workflow="leaf", step="review", branch_key="",
            parent_workflow="mid", parent_workflow_step="run_2",
        )
        # mid.run_2 is terminal -> mid done -> outer parent.next after mount
        assert end["ROLE_NAME"] == "review"
        assert end["WORKFLOW_STEP"] == "done"
        assert end["WORKFLOW_NAME"] == "parent"
        assert end["PARENT_WORKFLOW"] == ""
        assert end["PARENT_WORKFLOW_STEP"] == ""


def _import_workflow_step():
    import sys
    from nebuko.engine.lib.resolve_workflow import resolve_workflow_step
    return resolve_workflow_step


def _import_resolve_module():
    import sys
    import nebuko.engine.lib.resolve_workflow
    return resolve_workflow


class TestScaffoldShapeNestedEntry:
    """Exact issue #609 shape: a generated workflow (tmp-impl) whose every
    step references a nested sub-workflow (dev-review-loop) with per-step
    skills. Entry must resolve to the leaf's runnable step with the
    entry step's skill."""

    @pytest.fixture
    def scaffold_dir(self, tmp_path: Path) -> Path:
        steps = []
        for i in range(1, 7):
            steps.append({
                "name": f"step_{i}",
                "workflow": "dev-review-loop",
                "skill": [f"tmp_impl/skills/step_{i}"],
                "next": f"step_{i + 1}" if i < 6 else None,
            })
        (tmp_path / "tmp-impl.yaml").write_text(yaml.dump({
            "name": "tmp-impl",
            "description": "Dynamically generated per-step execution workflow",
            "steps": steps,
        }))
        (tmp_path / "dev-review-loop.yaml").write_text(yaml.dump({
            "name": "dev-review-loop",
            "description": "Reusable sub-workflow: implement + review loop",
            "steps": [
                {"name": "implement", "agent": "dev", "next": "review"},
                {
                    "name": "review",
                    "agent": "review",
                    "conditions": [
                        {"on_result": "approved", "next": None},
                        {"on_result": "rejected", "next": "implement"},
                        {"on_result": "default", "next": "implement"},
                    ],
                },
            ],
        }))
        return tmp_path

    def test_runtime_entry_resolves_to_leaf_with_step_skill(self, scaffold_dir):
        rwt = _import_workflow_step()
        r = rwt(
            task="dev", workflow_name="tmp-impl", step_name="step_1",
            branch_key="", workflows_dir=str(scaffold_dir),
            prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert r["WORKFLOW_NAME"] == "dev-review-loop"
        assert r["WORKFLOW_STEP"] == "implement"
        assert r["WORKFLOW_NEXT"] == "review"
        assert r["STEP_SKILLS"] == "tmp_impl/skills/step_1"
        # The mount-descent path carries no mode information, so its specs are
        # the inject default (issue #955).
        assert r["STEP_SKILL_SPECS"] == (
            '[{"mode":"inject","names":["tmp_impl/skills/step_1"]}]'
        )

    def test_trigger_side_marker_matches_leaf(self, scaffold_dir):
        """Plain-trigger entry marker descends to the same leaf the runtime
        path runs (no task=None / Unknown agent 'None')."""
        resolve = _import_resolver()
        m = resolve("tmp-impl", "step_1", str(scaffold_dir))
        assert m.kind is RouteKind.SUB_WORKFLOW_ENTER
        assert (m.task, m.step) == ("dev", "implement")
        assert (m.workflow, m.parent_workflow, m.parent_workflow_step) == (
            "dev-review-loop", "tmp-impl", "step_1"
        )
        """After step_1's loop completes, state lands on (tmp-impl, step_2):
        the descent re-enters dev-review-loop at implement and carries
        step_2's skill (not step_1's)."""
        rwt = _import_workflow_step()
        r = rwt(
            task="dev", workflow_name="tmp-impl", step_name="step_2",
            branch_key="", workflows_dir=str(scaffold_dir),
            prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert r["WORKFLOW_NAME"] == "dev-review-loop"
        assert r["WORKFLOW_STEP"] == "implement"
        assert r["STEP_SKILLS"] == "tmp_impl/skills/step_2"

    def test_last_step_reentry_carries_last_skill(self, scaffold_dir):
        """step_6 (next: null) re-enters the loop with step_6's skill — the
        final plan item, not a stale earlier one."""
        rwt = _import_workflow_step()
        r = rwt(
            task="dev", workflow_name="tmp-impl", step_name="step_6",
            branch_key="", workflows_dir=str(scaffold_dir),
            prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert r["STEP_SKILLS"] == "tmp_impl/skills/step_6"

    def test_internal_reject_loop_stays_inside_leaf(self, scaffold_dir):
        """The reject -> implement loop stays inside dev-review-loop: state
        at (dev-review-loop, implement) with the nesting parent pair on record
        does not fire return-to-parent — the parent pair is preserved and the
        config is the leaf's own."""
        rwt = _import_workflow_step()
        r = rwt(
            task="dev", workflow_name="dev-review-loop", step_name="implement",
            branch_key="rejected", workflows_dir=str(scaffold_dir),
            prompt_base="/tmp/prompts",
            parent_workflow="tmp-impl", parent_workflow_step="step_1",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert r["WORKFLOW_NAME"] == "dev-review-loop"
        assert r["WORKFLOW_STEP"] == "implement"
        assert r["WORKFLOW_NEXT"] == "review"
        assert r["PARENT_WORKFLOW"] == "tmp-impl"
        assert r["PARENT_WORKFLOW_STEP"] == "step_1"


class TestNestedEntrySkillPrecedence:
    """Skill merge during nested descent (issue #609): the entry step's
    skills take precedence over the leaf step's own skills; the leaf's
    skills are used when the entry declares none; an intermediate
    reference step's skill is carried down when the entry has none."""

    @pytest.fixture
    def skill_dirs(self, tmp_path: Path) -> Path:
        (tmp_path / "child1.yaml").write_text(yaml.dump({
            "name": "child1",
            "description": "Child with a skillead leaf",
            "steps": [
                {"name": "do", "agent": "dev", "skill": ["leaf1"], "next": None},
            ],
        }))
        # child1b: first step is a skillead reference to child2; the
        # grandchild leaf has no skills of its own.
        (tmp_path / "child1b.yaml").write_text(yaml.dump({
            "name": "child1b",
            "description": "Child whose first step is a reference",
            "steps": [
                {"name": "ref", "workflow": "child2", "skill": ["mid1"], "next": None},
            ],
        }))
        (tmp_path / "child2.yaml").write_text(yaml.dump({
            "name": "child2",
            "description": "Grandchild leaf without skills",
            "steps": [
                {"name": "do", "agent": "dev", "next": None},
            ],
        }))
        return tmp_path

    def _parent(self, tmp_path: Path, ref: str, skill=None):
        step = {"name": "mount", "workflow": ref, "next": None}
        if skill is not None:
            step["skill"] = skill
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent with a nested reference",
            "steps": [
                {"name": "start", "agent": "architect", "next": "mount"},
                step,
            ],
        }))

    def test_entry_skill_overrides_leaf_skill(self, skill_dirs):
        self._parent(skill_dirs, "child1", skill=["entry1"])
        rwt = _import_workflow_step()
        r = rwt(
            task="architect", workflow_name="parent", step_name="mount",
            branch_key="", workflows_dir=str(skill_dirs), prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert r["STEP_SKILLS"] == "entry1"

    def test_leaf_skill_used_when_entry_has_none(self, skill_dirs):
        self._parent(skill_dirs, "child1")
        rwt = _import_workflow_step()
        r = rwt(
            task="architect", workflow_name="parent", step_name="mount",
            branch_key="", workflows_dir=str(skill_dirs), prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert r["STEP_SKILLS"] == "leaf1"

    def test_intermediate_reference_skill_carried_down(self, skill_dirs):
        self._parent(skill_dirs, "child1b")
        rwt = _import_workflow_step()
        r = rwt(
            task="architect", workflow_name="parent", step_name="mount",
            branch_key="", workflows_dir=str(skill_dirs), prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert r["WORKFLOW_NAME"] == "child2"
        assert r["WORKFLOW_STEP"] == "do"
        assert r["STEP_SKILLS"] == "mid1"


class TestNestedEntryErrors:
    """Precise failures for nested descent: a leaf without a runnable
    source and a reference cycle (issue #609)."""

    def test_first_step_without_runnable_source_precise_reason(self, tmp_path: Path):
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent mounting a child",
            "steps": [
                {"name": "start", "agent": "architect", "next": "mount"},
                {"name": "mount", "workflow": "child", "next": None},
            ],
        }))
        # Child first step has a repo ref only: no agent/script/workflow
        # source to descend into — the reason names the unresolvable step
        # and entry rather than the misleading "Unknown agent 'None'".
        (tmp_path / "child.yaml").write_text(yaml.dump({
            "name": "child",
            "description": "Child without a runnable first step",
            "steps": [
                {"name": "first",
                 "repo": "https://github.com/owner/repo.git",
                 "ref": "main", "next": None},
            ],
        }))
        rwt = _import_workflow_step()
        r = rwt(
            task="architect", workflow_name="parent", step_name="mount",
            branch_key="", workflows_dir=str(tmp_path), prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "true"
        assert r["REASON"] == (
            "Sub-workflow 'child' step 'first' has no runnable source "
            "(no agent or script; entered via 'child')"
        )

    def test_reference_cycle_guard_stops_descent(self, tmp_path: Path):
        (tmp_path / "a.yaml").write_text(yaml.dump({
            "name": "a",
            "description": "A references B",
            "steps": [{"name": "s", "workflow": "b", "next": None}],
        }))
        (tmp_path / "b.yaml").write_text(yaml.dump({
            "name": "b",
            "description": "B references A (cycle)",
            "steps": [{"name": "s", "workflow": "a", "next": None}],
        }))
        rwt = _import_workflow_step()
        r = rwt(
            task="dev", workflow_name="a", step_name="s",
            branch_key="", workflows_dir=str(tmp_path), prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "true"
        assert "cycle detected" in r["REASON"]
        assert "'b'" in r["REASON"]

    def test_path_based_cycle_guard_stops_descent(self, tmp_path: Path, monkeypatch):
        """A self/cycle expressed as a *path* reference (``workflow:`` key
        holding a path value, whose file stem differs from the declared
        name) is still caught — review round 2, issue #609: the name-seed
        alone misses a path reference, so the canonical loaded-name check
        must fire. Path-like references resolve repo-root-relative, so the
        process cwd is moved to the fixture dir (as in
        test_path_based_workflow_reference)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "loop").mkdir()
        (tmp_path / "loop" / "workflow.yaml").write_text(yaml.dump({
            "name": "loop",
            "description": "Self-cycle via its own path",
            "steps": [
                {"name": "s", "workflow": "loop/workflow.yaml", "next": None},
            ],
        }))
        (tmp_path / "start.yaml").write_text(yaml.dump({
            "name": "start",
            "description": "Mounts the self-cycling child by path",
            "steps": [
                {"name": "go", "agent": "dev", "next": "enter"},
                {"name": "enter", "workflow": "loop/workflow.yaml", "next": None},
            ],
        }))
        rwt = _import_workflow_step()
        r = rwt(
            task="dev", workflow_name="start", step_name="enter",
            branch_key="", workflows_dir=str(tmp_path), prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "true"
        assert "cycle detected" in r["REASON"]

    def test_max_depth_bound_stops_deep_chain(self, tmp_path: Path, monkeypatch):
        """A deep non-cyclic chain terminates at the depth bound instead of
        unbounded descent (review round 2, issue #609). The bound is
        monkeypatched down so the fixture stays small."""
        rmod = _import_resolve_module()
        monkeypatch.setattr(rmod, "_MAX_SUB_WORKFLOW_DEPTH", 1)
        # chain_a -> chain_b -> chain_c: the entry loads chain_b; the
        # descent loads chain_c (depth 1) and then hits the bound before
        # loading the next level, so chain_d need not exist.
        for name, nxt in (
            ("chain_a", "chain_b"),
            ("chain_b", "chain_c"),
            ("chain_c", "chain_d"),
        ):
            (tmp_path / f"{name}.yaml").write_text(yaml.dump({
                "name": name,
                "description": f"{name} references {nxt}",
                "steps": [{"name": "s", "workflow": nxt, "next": None}],
            }))
        rwt = _import_workflow_step()
        r = rwt(
            task="dev", workflow_name="chain_a", step_name="s",
            branch_key="", workflows_dir=str(tmp_path), prompt_base="/tmp/prompts",
        )
        assert r["SKIP_AGENT"] == "true"
        assert "max sub-workflow nesting depth" in r["REASON"]


class TestLinearScriptSuccessor:
    """A linear ``script:`` successor reached via return-to-parent must emit
    the deterministic script contract (issue #541 / #609 review round 2):
    the GHA gate runs the script only when ``SKIP_AGENT=true`` AND
    ``RUN_SCRIPT=true``; the old ``SKIP_AGENT=false`` shape fell through to
    the agent-run section with an empty role."""

    def test_script_successor_after_sub_workflow_emit_contract(self, tmp_path: Path):
        # child.end is terminal; completing it returns to parent where the
        # next step after the mount is a linear script step.
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent with a script successor after the mount",
            "steps": [
                {"name": "start", "agent": "architect", "next": "mount"},
                {"name": "mount", "workflow": "child", "next": "post"},
                {
                    "name": "post",
                    "script": {
                        "command": "scripts/post.py",
                        "args": ["--issue", "42"],
                        "timeout_minutes": 5,
                    },
                    "next": None,
                },
            ],
        }))
        (tmp_path / "child.yaml").write_text(yaml.dump({
            "name": "child",
            "description": "Child with a single terminal agent step",
            "steps": [{"name": "end", "agent": "dev", "next": None}],
        }))
        rwt = _import_workflow_step()
        r = rwt(
            task="dev", workflow_name="child", step_name="end", branch_key="",
            parent_workflow="parent", parent_workflow_step="mount",
            workflows_dir=str(tmp_path), prompt_base="/tmp/prompts",
        )
        # Same contract as the nested-entry script branch and build_result's
        # run_script path: skip the agent, run the script.
        assert r["SKIP_AGENT"] == "true"
        assert r["RUN_SCRIPT"] == "true"
        assert r["ROLE_NAME"] == ""
        assert "REASON" not in r
        assert r["SCRIPT_PATH"] == "scripts/post.py"
        assert r["SCRIPT_ARGS"] == '["--issue", "42"]'
        assert r["SCRIPT_TIMEOUT_MINUTES"] == "5"
        assert r["WORKFLOW_NAME"] == "parent"
        assert r["WORKFLOW_STEP"] == "post"


class TestNestedFetchOnMiss:
    """Two-level fetch-on-miss: a branch-mounted child absent locally,
    whose first step references a further branch-mounted child. Each
    reference is fetched exactly once per entry (issue #604/#609)."""

    def test_each_reference_fetched_once(self, tmp_path: Path, monkeypatch):
        mod = _import_resolve_module()
        fetched_dir = tmp_path / "fetched"
        fetched_dir.mkdir()
        (fetched_dir / "inner.yaml").write_text(yaml.dump({
            "name": "inner",
            "description": "Branch-mounted grandchild",
            "steps": [{"name": "do", "agent": "dev", "next": None}],
        }))
        (fetched_dir / "child.yaml").write_text(yaml.dump({
            "name": "child",
            "description": "Branch-mounted child",
            "steps": [
                {"name": "run", "workflow": "inner/wf.yaml", "next": None},
            ],
        }))
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Local parent",
            "steps": [
                {"name": "start", "agent": "architect", "next": "mount"},
                {"name": "mount", "workflow": "tmp_impl/workflow.yaml", "next": None},
            ],
        }))

        calls: list = []

        def fake_fetch(ref, scaffold_branch):
            calls.append(ref)
            mapping = {
                "tmp_impl/workflow.yaml": str(fetched_dir / "child.yaml"),
                "inner/wf.yaml": str(fetched_dir / "inner.yaml"),
            }
            return mapping.get(ref)

        monkeypatch.setattr(mod, "_scaffold_fetch", fake_fetch)

        rwt = _import_workflow_step()
        r = rwt(
            task="architect", workflow_name="parent", step_name="mount",
            branch_key="", workflows_dir=str(tmp_path), prompt_base="/tmp/prompts",
            scaffold_branch="feature/issue-590-engine-core",
        )
        assert r["SKIP_AGENT"] == "false"
        assert r["ROLE_NAME"] == "dev"
        assert r["WORKFLOW_NAME"] == "inner"
        assert r["WORKFLOW_STEP"] == "do"
        assert calls.count("tmp_impl/workflow.yaml") == 1
        assert calls.count("inner/wf.yaml") == 1


class TestCircularReference:
    """Test circular reference detection in step transitions."""

    @pytest.fixture
    def circular_workflows_dir(self, tmp_path: Path) -> Path:
        """Create a workflow where step A → B → A (circular)."""
        (tmp_path / "circular.yaml").write_text(yaml.dump({
            "name": "circular",
            "description": "Circular reference workflow",
            "steps": [
                {"name": "step_a", "agent": "dev", "next": "step_b"},
                {"name": "step_b", "agent": "review", "next": "step_a"},
            ],
        }))
        return tmp_path

    def test_circular_reference_resolves_without_error(self, circular_workflows_dir):
        """resolve_next_from_workflow should handle circular refs gracefully (single-step)."""
        resolve = _import_resolver()
        result = resolve(
            "circular", "step_a", str(circular_workflows_dir),
        )
        # Single-step resolution returns the next step regardless of cycles
        assert (result.task, result.step) == ("review", "step_b")

    def test_circular_reference_reverse(self, circular_workflows_dir):
        """Resolving from step_b → step_a also works."""
        resolve = _import_resolver()
        result = resolve(
            "circular", "step_b", str(circular_workflows_dir),
        )
        assert (result.task, result.step) == ("dev", "step_a")


class TestMissingNextStep:
    """Test missing next step reference in YAML."""

    @pytest.fixture
    def broken_next_workflows_dir(self, tmp_path: Path) -> Path:
        """Create a workflow where step.next points to a non-existent step name."""
        (tmp_path / "broken_next.yaml").write_text(yaml.dump({
            "name": "broken_next",
            "description": "Broken next step reference",
            "steps": [
                {"name": "start", "agent": "architect", "next": "ghost"},
                {"name": "real_step", "agent": "dev", "next": None},
            ],
        }))
        return tmp_path

    def test_missing_next_step_returns_none(self, broken_next_workflows_dir):
        """When step.next references a non-existent step, return None."""
        resolve = _import_resolver()
        result = resolve(
            "broken_next", "start", str(broken_next_workflows_dir),
        )
        assert result.kind is RouteKind.UNRESOLVED

    def test_missing_next_step_with_hint(self, broken_next_workflows_dir):
        """When step.next references a non-existent step, use agent_hint."""
        resolve = _import_resolver()
        result = resolve(
            "broken_next", "start", str(broken_next_workflows_dir),
            agent_hint="dev",
        )
        # agent_hint only applies for terminal/missing-current-step; not for broken next
        # The current step exists and is not terminal, so it proceeds to look up the target.
        # Target "ghost" doesn't exist -> typed UNRESOLVED (no hint fallback here)
        assert result.kind is RouteKind.UNRESOLVED


class TestConditionsOnlyWorkflow:
    """Test workflow with only conditions and no default next."""

    @pytest.fixture
    def conditions_only_workflows_dir(self, tmp_path: Path) -> Path:
        """Create a workflow where a step has conditions but no default next."""
        (tmp_path / "conditions_only.yaml").write_text(yaml.dump({
            "name": "conditions_only",
            "description": "Workflow with conditions-only step",
            "steps": [
                {"name": "start", "agent": "architect", "next": "decision"},
                {
                    "name": "decision",
                    "agent": "review",
                    "conditions": [
                        {"on_result": "approved", "next": "proceed"},
                        {"on_result": "rejected", "next": "start"},
                    ],
                    "next": None,
                },
                {"name": "proceed", "agent": "dev", "next": None},
            ],
        }))
        return tmp_path

    def test_conditions_only_with_matching_branch(self, conditions_only_workflows_dir):
        """Matching branch result resolves correctly."""
        resolve = _import_resolver()
        result = resolve(
            "conditions_only", "decision", str(conditions_only_workflows_dir),
            branch_result="approved",
        )
        assert (result.task, result.step, result.branch) == ("dev", "proceed", "approved")

    def test_conditions_only_no_branch_uses_linear_next(self, conditions_only_workflows_dir):
        """Without branch_result, use the linear 'next' field (which is None here)."""
        resolve = _import_resolver()
        result = resolve(
            "conditions_only", "decision", str(conditions_only_workflows_dir),
        )
        # No branch_result and no linear next -> the step declares conditions
        # but nothing matched, which is a typed UNRESOLVED (issue #964), not a
        # silent declared finish.
        assert result.kind is RouteKind.UNRESOLVED
        assert "none matched" in result.reason

    def test_conditions_only_no_default_fallback(self, conditions_only_workflows_dir):
        """Unmatched branch with no default condition returns None."""
        resolve = _import_resolver()
        result = resolve(
            "conditions_only", "decision", str(conditions_only_workflows_dir),
            branch_result="unknown",
        )
        # No matching condition and no "default" condition -> typed UNRESOLVED
        assert result.kind is RouteKind.UNRESOLVED
        assert "unknown" in result.reason

    def test_conditions_only_branchless_honors_default_condition(
        self, tmp_path: Path
    ):
        """Branch-less dispatch on a conditions-only step honors the
        ``default`` condition instead of dead-ending (issue #618)."""
        (tmp_path / "branchless_default.yaml").write_text(yaml.dump({
            "name": "branchless_default",
            "description": "Conditions-only step with a default condition",
            "steps": [
                {
                    "name": "decision",
                    "agent": "architect",
                    "conditions": [
                        {"on_result": "approved", "next": "proceed"},
                        {"on_result": "rejected", "next": "decision"},
                        {"on_result": "default", "next": "proceed"},
                    ],
                    "next": None,
                },
                {"name": "proceed", "agent": "dev", "next": None},
            ],
        }))
        resolve = _import_resolver()
        result = resolve("branchless_default", "decision", str(tmp_path))
        assert (result.task, result.step, result.branch) == ("dev", "proceed", "default")

    def test_conditions_only_branchless_no_default_is_terminal(
        self, conditions_only_workflows_dir
    ):
        """Branch-less dispatch on a conditions-only step with no
        ``default`` condition stays terminal (documents the #617 edge)."""
        resolve = _import_resolver()
        result = resolve("conditions_only", "decision", str(conditions_only_workflows_dir))
        assert result.kind is RouteKind.UNRESOLVED
        assert "none matched" in result.reason


class TestEmptyWorkflowDefinition:
    """Test empty workflow definition."""

    @pytest.fixture
    def empty_workflows_dir(self, tmp_path: Path) -> Path:
        """Create a workflow with no steps."""
        (tmp_path / "empty.yaml").write_text(yaml.dump({
            "name": "empty",
            "description": "Empty workflow",
            "steps": [],
        }))
        return tmp_path

    def test_empty_workflow_returns_none(self, empty_workflows_dir):
        """Empty workflow (no steps) returns None."""
        resolve = _import_resolver()
        result = resolve(
            "empty", "anything", str(empty_workflows_dir),
        )
        assert result is None

    def test_empty_workflow_with_hint(self, empty_workflows_dir):
        """Empty workflow with agent_hint still falls back to hint."""
        resolve = _import_resolver()
        result = resolve(
            "empty", "anything", str(empty_workflows_dir),
            agent_hint="dev",
        )
        assert result.task == "dev"


def _real_workflows_dir() -> Path:
    """The shipped workflow dir, not the per-repo ``.nebuko`` override.

    ``config_dir`` prefers a checkout-local override directory whose contents
    vary per machine (it may even be empty), so the shipped definitions are
    what these resolution assertions must read.
    """
    from nebuko.engine.lib.default_paths import checkout_root
    return Path(checkout_root()) / "defaults" / "workflows"


class TestDefaultWorkflowWithRealFile:
    """Test resolve_next_from_workflow against the real workflows/default.yaml.

    Regression test for issue #377 — ensures dispatch.advance from the
    'analyze' step correctly resolves to the 'context' agent.
    """

    def test_analyze_resolves_to_context(self):
        """dispatch.advance from analyze step → context agent (context step)."""
        resolve = _import_resolver()
        result = resolve("default", "analyze", str(_real_workflows_dir()))
        assert (result.task, result.step) == ("context", "context")

    def test_context_resolves_to_architect_plan(self):
        """dispatch.advance from context step → architect agent (plan step)."""
        resolve = _import_resolver()
        result = resolve("default", "context", str(_real_workflows_dir()))
        assert (result.task, result.step) == ("architect", "plan")

    def test_plan_resolves_to_dev_implement(self):
        """dispatch.advance from plan step → dev agent (implement step)."""
        resolve = _import_resolver()
        result = resolve("default", "plan", str(_real_workflows_dir()))
        assert result.kind is RouteKind.ROUTE
        assert (result.task, result.step) == ("dev", "implement")

    def test_implement_resolves_to_review(self):
        """dispatch.advance from implement step → review agent."""
        resolve = _import_resolver()
        result = resolve("default", "implement", str(_real_workflows_dir()))
        assert (result.task, result.step) == ("review", "review")

    def test_review_is_terminal_returns_none(self):
        """Review is the terminal step — returns None."""
        resolve = _import_resolver()
        result = resolve("default", "review", str(_real_workflows_dir()))
        # ``review`` declares conditions and matched none, so it is a typed
        # UNRESOLVED rather than a declared finish (issue #964).
        assert result.kind is RouteKind.UNRESOLVED

    def test_body_from_issue_resolves_to_context(self):
        """End-to-end test: parse the exact body text from issue #377 and verify context is resolved.

        The architect agent finishes the 'analyze' step with dispatch.advance (no task field).
        The body contains a WORKFLOW_STATE block. The resolver should extract the state,
        find the next step in default.yaml ('context'), and return agent 'context'.
        """
        resolve = _import_resolver()

        import sys
        from nebuko.engine.lib.workflow_state.features.get_last_state.get_last_state import extract_state_from_body

        body = (
            "<summary>🤖 Workflow State (v1)</summary>\n"
            "\n"
            "```json\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            '{\n'
            '  "workflow": "default",\n'
            '  "step": "analyze",\n'
            '  "agent": "architect",\n'
            '  "phase": "analysis",\n'
            '  "history": [\n'
            '    {\n'
            '      "step": "analyze",\n'
            '      "agent": "architect",\n'
            '      "summary": "Architect answer summary",\n'
            # ``turn_outcome`` is required on every entry (issue #964); the
            # issue-#377 body predates it, so the replay states the fact.
            '      "turn_outcome": {"status": "completed", "appreciation": "approved"}\n'
            "    }\n"
            "  ],\n"
            '  "metrics": {\n'
            '    "run_id": "d17d26d4-c4de-4808-b80b-e353d79e0563",\n'
            '    "model_name": "default",\n'
            '    "agent_role": "architect",\n'
            '    "input_tokens": 8069,\n'
            '    "output_tokens": 276\n'
            "  },\n"
            '  "issue": {\n'
            '    "number": 377,\n'
            '    "url": "https://github.com/olivier-norture/git-flow-ai/issues/377",\n'
            '    "title": "fix workflow force-next-step requires an active workflow",\n'
            '    "author": "olivier-norture",\n'
            '    "repo": "olivier-norture/git-flow-ai"\n'
            "  }\n"
            "}\n"
            "<!-- WORKFLOW_STATE_END -->\n"
            "```\n"
        )

        state = extract_state_from_body(body)
        assert state is not None
        assert state.workflow == "default"
        assert state.step == "analyze"
        assert state.agent == "architect"

        result = resolve(state.workflow, state.step, str(_real_workflows_dir()))
        assert (result.task, result.step) == ("context", "context")


def _import_return_marker_for_parent():
    import sys
    from nebuko.engine.lib.resolve_workflow import _return_marker_for_parent
    return _return_marker_for_parent


class TestParentReturnFailureSurface:
    """A failed child re-entry while unwinding to the parent must stay a
    typed failure marker (``SKIP_AGENT``), never be decorated as a
    successful entry marker (issue #671 carry-forward)."""

    def test_unresolvable_remount_is_surfaced_unchanged(self, tmp_path: Path):
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent with a conditional remount",
            "steps": [
                {
                    "name": "anchor",
                    "agent": "architect",
                    "conditions": [
                        {"on_result": "approved", "next": "remount"},
                    ],
                    "next": None,
                },
                {"name": "remount", "workflow": "broken"},
            ],
        }))
        (tmp_path / "broken.yaml").write_text(yaml.dump({
            "name": "broken",
            "description": "Child without a runnable first step",
            "steps": [
                {
                    "name": "first",
                    "repo": "https://github.com/owner/repo.git",
                    "ref": "main",
                    "next": None,
                },
            ],
        }))

        marker = _import_return_marker_for_parent()(
            ("parent", "anchor"), "approved", str(tmp_path),
        )

        assert marker is not None
        assert marker.get("SKIP_AGENT") == "true"
        assert "no runnable source" in marker["REASON"]
        # The failure dict must not be decorated with a branch key.
        assert "branch" not in marker


class TestStepComplexityEmission:
    """Per-step declared complexity threading (issue #787)."""

    @pytest.fixture
    def complexity_workflows_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "tiered.yaml").write_text(yaml.dump({
            "name": "tiered",
            "description": "Workflow with a declared per-step tier",
            "steps": [
                {"name": "analyze", "agent": "architect", "complexity": "low", "next": "plan"},
                {"name": "plan", "agent": "architect", "next": None},
            ],
        }))
        return tmp_path

    def test_declared_tier_is_emitted(self, complexity_workflows_dir):
        rwt = _import_workflow_step()
        r = rwt(
            task="architect", workflow_name="tiered", step_name="analyze",
            branch_key="", workflows_dir=str(complexity_workflows_dir),
            prompt_base="/tmp/prompts",
        )
        assert r["STEP_COMPLEXITY"] == "low"

    def test_undeclared_tier_emits_empty_string(self, complexity_workflows_dir):
        rwt = _import_workflow_step()
        r = rwt(
            task="architect", workflow_name="tiered", step_name="plan",
            branch_key="", workflows_dir=str(complexity_workflows_dir),
            prompt_base="/tmp/prompts",
        )
        assert r["STEP_COMPLEXITY"] == ""


class TestResolveComplexityTier:
    """The single resolution policy: dispatch > step > "medium" (issue #787)."""

    def test_explicit_dispatch_wins_over_step(self):
        resolve = _import_resolve_module()
        assert resolve.resolve_complexity_tier("high", "low") == "high"

    def test_step_tier_used_when_dispatch_unset(self):
        resolve = _import_resolve_module()
        assert resolve.resolve_complexity_tier("", "low") == "low"

    def test_defaults_to_medium_when_both_unset(self):
        resolve = _import_resolve_module()
        assert resolve.resolve_complexity_tier("", "") == "medium"

    def test_none_values_count_as_unset(self):
        resolve = _import_resolve_module()
        assert resolve.resolve_complexity_tier(None, None) == "medium"
        assert resolve.resolve_complexity_tier(None, "high") == "high"
