"""Tests for workflow resolver (resolve_workflow.py).

Unit tests for workflow models have been migrated to co-located tests under
config/sub_domains/. This file retains only integration tests for
resolve_workflow which depend on the config package.
"""

import os
import tempfile
from pathlib import Path

import yaml


# ── Skills Helper Tests ──────────────────────────────────────────────


class TestSkillsToString:
    """Test _skills_to_string helper from resolve_workflow."""

    def _import_helper(self):
        import importlib
        import sys
        # Ensure the scripts directory is on the path
        if "." not in sys.path:
            sys.path.insert(0, ".")
        resolve_workflow = importlib.import_module("resolve_workflow")
        return resolve_workflow._skills_to_string

    def test_none_returns_empty(self):
        to_str = self._import_helper()
        assert to_str(None) == ""

    def test_empty_list_returns_empty(self):
        to_str = self._import_helper()
        assert to_str([]) == ""

    def test_single_skill(self):
        to_str = self._import_helper()
        assert to_str(["python"]) == "python"

    def test_multiple_skills(self):
        to_str = self._import_helper()
        assert to_str(["python", "rust"]) == "python,rust"


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


# ── Sub-workflow Resolver Tests ────────────────────────────────────────


class TestStepPromptFile:
    """STEP_PROMPT_FILE emission and content (issue #551)."""

    PROMPT = (
        "Analyze the transcription and provide me:\n"
        "a short summary (~5-10 sentences)\n"
        "5 Key points\n"
    )
    PROMPT_PATH = "/tmp/step_prompt.md"

    def _wf(self, steps):
        return {
            "name": "wf",
            "description": "prompt wf",
            "trigger": {"agents": ["genius"]},
            "steps": steps,
        }

    def _resolve(self, request, tmpdir):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from resolve_workflow import resolve_workflow_routing
        return resolve_workflow_routing(request, tmpdir, ".agents/prompts")

    def _cleanup(self):
        if os.path.exists(self.PROMPT_PATH):
            os.unlink(self.PROMPT_PATH)

    def test_first_step_with_prompt_writes_file(self):
        self._cleanup()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                (Path(tmpdir) / "wf.yaml").write_text(yaml.dump(self._wf([
                    {"name": "analyze", "agent": "genius", "prompt": self.PROMPT, "next": None},
                ])))
                result = self._resolve({"task": "genius", "workflow": "wf"}, tmpdir)
                assert result["STEP_PROMPT_FILE"] == self.PROMPT_PATH
                assert Path(self.PROMPT_PATH).read_text() == self.PROMPT
        finally:
            self._cleanup()

    def test_next_step_with_prompt_writes_file(self):
        self._cleanup()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                (Path(tmpdir) / "wf.yaml").write_text(yaml.dump(self._wf([
                    {"name": "first", "agent": "genius", "next": "second"},
                    {"name": "second", "agent": "dev", "prompt": self.PROMPT, "next": None},
                ])))
                result = self._resolve(
                    {"task": "dev", "workflow": "wf", "step": "second"}, tmpdir
                )
                assert result["ROLE_NAME"] == "dev"
                assert result["STEP_PROMPT_FILE"] == self.PROMPT_PATH
                assert Path(self.PROMPT_PATH).read_text() == self.PROMPT
        finally:
            self._cleanup()

    def test_step_without_prompt_emits_empty(self):
        self._cleanup()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                (Path(tmpdir) / "wf.yaml").write_text(yaml.dump(self._wf([
                    {"name": "only", "agent": "dev", "next": None},
                ])))
                result = self._resolve({"task": "dev", "workflow": "wf"}, tmpdir)
                assert result["STEP_PROMPT_FILE"] == ""
                assert not os.path.exists(self.PROMPT_PATH)
        finally:
            self._cleanup()

    def test_step_without_prompt_removes_stale_file(self):
        # A previous run on this runner left a file behind; resolution of a
        # step without a prompt must remove it so CI sees no injection.
        Path(self.PROMPT_PATH).write_text("stale")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                (Path(tmpdir) / "wf.yaml").write_text(yaml.dump(self._wf([
                    {"name": "only", "agent": "dev", "next": None},
                ])))
                result = self._resolve({"task": "dev", "workflow": "wf"}, tmpdir)
                assert result["STEP_PROMPT_FILE"] == ""
                assert not os.path.exists(self.PROMPT_PATH)
        finally:
            self._cleanup()

    def test_sub_workflow_entry_uses_child_step_prompt(self):
        self._cleanup()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                (Path(tmpdir) / "parent.yaml").write_text(yaml.dump(self._wf([
                    {"name": "start", "agent": "genius", "next": "subtask"},
                    {"name": "subtask", "workflow": "child", "next": None},
                ])))
                (Path(tmpdir) / "child.yaml").write_text(yaml.dump(self._wf([
                    {"name": "scan", "agent": "ctx", "prompt": self.PROMPT, "next": None},
                ])))
                result = self._resolve(
                    {"task": "ctx", "workflow": "parent", "step": "subtask"}, tmpdir
                )
                assert result["WORKFLOW_NAME"] != "parent"
                assert result["WORKFLOW_STEP"] == "scan"
                assert result["STEP_PROMPT_FILE"] == self.PROMPT_PATH
                assert Path(self.PROMPT_PATH).read_text() == self.PROMPT
        finally:
            self._cleanup()


class TestSubWorkflowResolution:
    """Test sub-workflow entry, traversal, and return-to-parent."""

    def _import_resolver(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from resolve_workflow import resolve_workflow_routing
        return resolve_workflow_routing

    def test_sub_workflow_entry_to_first_step(self):
        """When a step references a workflow, resolve to its first step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create parent workflow with sub-workflow step
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "start", "agent": "genius", "next": "subtask"},
                    {
                        "name": "subtask",
                        "workflow": "child",
                        "next": "end",
                    },
                    {"name": "end", "agent": "dev", "next": None},
                ],
            }))
            # Create child workflow
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Child workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "scan", "agent": "ctx", "next": "report"},
                    {"name": "report", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "parent", "step": "subtask"},
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "ctx"
            assert result["WORKFLOW_NAME"] == "child"
            assert result["WORKFLOW_STEP"] == "scan"
            assert result["WORKFLOW_NEXT"] == "report"
            assert result["PARENT_WORKFLOW"] == "parent"
            assert result["PARENT_WORKFLOW_STEP"] == "subtask"
            assert result["SKIP_AGENT"] == "false"

    def test_sub_workflow_with_custom_path(self):
        """Sub-workflow can use path: to specify a custom location."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "custom"
            subdir.mkdir()
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {
                        "name": "subtask",
                        "workflow": "external",
                        "path": str(subdir / "custom-wf.yaml"),
                        "next": None,
                    },
                ],
            }))
            (subdir / "custom-wf.yaml").write_text(yaml.dump({
                "name": "external",
                "description": "External workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "run", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "parent", "step": "subtask"},
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NAME"] == "external"
            assert result["WORKFLOW_STEP"] == "run"
            assert result["PARENT_WORKFLOW"] == "parent"

    def test_sub_workflow_terminal_returns_to_parent(self):
        """Terminal step in sub-workflow returns control to parent's next step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "start", "agent": "genius", "next": "subtask"},
                    {"name": "subtask", "workflow": "child", "next": "finalize"},
                    {"name": "finalize", "agent": "dev", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Child workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "only-step", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {
                    "task": "review",
                    "workflow": "child",
                    "step": "only-step",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NAME"] == "parent"
            assert result["WORKFLOW_STEP"] == "finalize"
            assert result["PARENT_WORKFLOW"] == ""
            assert result["PARENT_WORKFLOW_STEP"] == ""

    def test_sub_workflow_multi_step_completion(self):
        """Multi-step sub-workflow: second step continues normally, last step returns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "subtask", "workflow": "child", "next": "after"},
                    {"name": "after", "agent": "genius", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Child workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "first", "agent": "ctx", "next": "last"},
                    {"name": "last", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            # Step 1: enter sub-workflow (already tested above)

            # Step 2: continue within child (non-terminal step)
            result = resolve(
                {
                    "task": "ctx",
                    "workflow": "child",
                    "step": "first",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "ctx"
            assert result["WORKFLOW_NEXT"] == "last"
            assert result["PARENT_WORKFLOW"] == "parent"

            # Step 3: terminal step → return to parent
            result = resolve(
                {
                    "task": "review",
                    "workflow": "child",
                    "step": "last",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "genius"
            assert result["WORKFLOW_NAME"] == "parent"
            assert result["WORKFLOW_STEP"] == "after"
            assert result["PARENT_WORKFLOW"] == ""

    def test_plan_workflow_entry_emits_plan_position(self):
        """Entering a branch-mounted plan (one step_N per item) records the plan step.

        First entry: current step is the mount point (``sub_impl``); the plan's
        first step (``step_1``) is entered, so the pointer is derived from the
        child. Non-plan children keep the keys empty.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "sub_impl", "workflow": "plan", "next": "sub_clean"},
                    {"name": "sub_clean", "agent": "dev", "next": None},
                ],
            }))
            (Path(tmpdir) / "plan.yaml").write_text(yaml.dump({
                "name": "plan",
                "description": "Plan workflow (one step_N per plan item)",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "step_1", "agent": "dev", "next": "step_2"},
                    {"name": "step_2", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "parent", "step": "sub_impl"},
                tmpdir, ".agents/prompts",
            )
            # Flat parent pair unchanged (existing invariant)
            assert result["PARENT_WORKFLOW"] == "parent"
            assert result["PARENT_WORKFLOW_STEP"] == "sub_impl"
            # Plan position pointer emitted for the entered step
            assert result["PARENT_PLAN_STEP_INDEX"] == "1"
            assert result["PARENT_PLAN_TOTAL_STEPS"] == "2"

    def test_non_plan_child_leaves_plan_position_empty(self):
        """Non-plan sub-workflow children emit empty plan-position keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "subtask", "workflow": "child", "next": "end"},
                    {"name": "end", "agent": "dev", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Non-plan child",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "scan", "agent": "ctx", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "parent", "step": "subtask"},
                tmpdir, ".agents/prompts",
            )
            assert result["PARENT_WORKFLOW"] == "parent"
            assert result["PARENT_PLAN_STEP_INDEX"] == ""
            assert result["PARENT_PLAN_TOTAL_STEPS"] == ""

    def test_child_with_single_step_named_like_plan_keeps_position_empty(self):
        """A non-plan child whose first step is named ``step_N`` (but whose
        steps do not form a contiguous ``step_1..step_N`` sequence) must not
        produce a bogus plan pointer (review round 2, issue 5).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "subtask", "workflow": "child", "next": "end"},
                    {"name": "end", "agent": "dev", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Non-plan child with one step_N-named step",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "step_1", "agent": "ctx", "next": "finish"},
                    {"name": "finish", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "parent", "step": "subtask"},
                tmpdir, ".agents/prompts",
            )
            assert result["PARENT_PLAN_STEP_INDEX"] == ""
            assert result["PARENT_PLAN_TOTAL_STEPS"] == ""

    def test_plan_workflow_return_leg_emits_next_plan_position(self):
        """Terminal return re-entering the plan's next step_N carries its index.

        Mirrors the plan-orchestration shape: terminal leaf inside the step's
        sub-workflow returns to the plan, whose next step (``step_2``) re-enters
        its sub-workflow via _next_step_resolution — the pointer must reflect
        the step being entered, not the previous one.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "plan.yaml").write_text(yaml.dump({
                "name": "plan",
                "description": "Plan workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "step_1", "workflow": "drl", "next": "step_2"},
                    {"name": "step_2", "workflow": "drl", "next": None},
                ],
            }))
            (Path(tmpdir) / "drl.yaml").write_text(yaml.dump({
                "name": "drl",
                "description": "Per-step sub-workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "implement", "agent": "dev", "next": "review"},
                    {"name": "review", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            # Terminal leaf in drl (step_1's sub-workflow) → return to plan,
            # re-entering step_2 → drl.implement with the step_2 pointer.
            result = resolve(
                {
                    "task": "review",
                    "workflow": "drl",
                    "step": "review",
                    "parent_workflow": "plan",
                    "parent_workflow_step": "step_1",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["WORKFLOW_NAME"] == "drl"
            assert result["WORKFLOW_STEP"] == "implement"
            assert result["PARENT_WORKFLOW"] == "plan"
            assert result["PARENT_WORKFLOW_STEP"] == "step_2"
            assert result["PARENT_PLAN_STEP_INDEX"] == "2"
            assert result["PARENT_PLAN_TOTAL_STEPS"] == "2"

    def test_plan_workflow_return_leg_emits_completed_plan_position(self):
        """Terminal return leg also names the *completed* plan step.

        Review round 2 (issue #613): at the boundary the refreshed
        PARENT_PLAN_* keys point at the step being entered; the
        COMPLETED_PLAN_* keys must carry the step whose sub-workflow just
        reached its terminal step, and WORKFLOW_STEP_TERMINAL signals the
        terminal return leg so the completion marker posts the right index.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "plan.yaml").write_text(yaml.dump({
                "name": "plan",
                "description": "3-step plan workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "step_1", "workflow": "drl", "next": "step_2"},
                    {"name": "step_2", "workflow": "drl", "next": "step_3"},
                    {"name": "step_3", "workflow": "drl", "next": None},
                ],
            }))
            (Path(tmpdir) / "drl.yaml").write_text(yaml.dump({
                "name": "drl",
                "description": "Per-step sub-workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "implement", "agent": "dev", "next": "review"},
                    {"name": "review", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            # step_1's terminal leaf → return leg re-enters step_2.
            result = resolve(
                {
                    "task": "review",
                    "workflow": "drl",
                    "step": "review",
                    "parent_workflow": "plan",
                    "parent_workflow_step": "step_1",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["WORKFLOW_NAME"] == "drl"
            assert result["WORKFLOW_STEP"] == "implement"
            # Refreshed pointer = step being entered (invariant, unchanged)
            assert result["PARENT_PLAN_STEP_INDEX"] == "2"
            # Completed pointer = step just finished (new signal)
            assert result["WORKFLOW_STEP_TERMINAL"] == "true"
            assert result["COMPLETED_PLAN_STEP_INDEX"] == "1"
            assert result["COMPLETED_PLAN_TOTAL_STEPS"] == "3"

            # step_2's terminal leaf → return leg re-enters step_3.
            result = resolve(
                {
                    "task": "review",
                    "workflow": "drl",
                    "step": "review",
                    "parent_workflow": "plan",
                    "parent_workflow_step": "step_2",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["WORKFLOW_NAME"] == "drl"
            assert result["PARENT_PLAN_STEP_INDEX"] == "3"
            assert result["WORKFLOW_STEP_TERMINAL"] == "true"
            assert result["COMPLETED_PLAN_STEP_INDEX"] == "2"
            assert result["COMPLETED_PLAN_TOTAL_STEPS"] == "3"

            # step_3's terminal leaf → plan terminates (no return leg).
            result = resolve(
                {
                    "task": "review",
                    "workflow": "drl",
                    "step": "review",
                    "parent_workflow": "plan",
                    "parent_workflow_step": "step_3",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["SKIP_AGENT"] == "true"
            assert result["WORKFLOW_STEP_TERMINAL"] == "true"
            assert result["COMPLETED_PLAN_STEP_INDEX"] == "3"
            assert result["COMPLETED_PLAN_TOTAL_STEPS"] == "3"

    def test_non_plan_return_leg_emits_terminal_without_plan_position(self):
        """Non-plan terminal return leg: terminal flag set, COMPLETED_* empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "subtask", "workflow": "child", "next": "end"},
                    {"name": "end", "agent": "dev", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Non-plan child",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "scan", "agent": "ctx", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {
                    "task": "ctx",
                    "workflow": "child",
                    "step": "scan",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["WORKFLOW_NAME"] == "parent"
            assert result["WORKFLOW_STEP"] == "end"
            assert result["WORKFLOW_STEP_TERMINAL"] == "true"
            assert result["COMPLETED_PLAN_STEP_INDEX"] == ""
            assert result["COMPLETED_PLAN_TOTAL_STEPS"] == ""

    def test_non_terminal_steps_do_not_emit_terminal_flag(self):
        """Non-terminal resolutions carry no terminal signal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "subtask", "workflow": "child", "next": "end"},
                    {"name": "end", "agent": "dev", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Multi-step child",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "first", "agent": "ctx", "next": "last"},
                    {"name": "last", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            # Non-terminal step inside the child.
            result = resolve(
                {
                    "task": "ctx",
                    "workflow": "child",
                    "step": "first",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["WORKFLOW_NEXT"] == "last"
            assert result.get("WORKFLOW_STEP_TERMINAL", "") in ("", None)

    def test_normal_step_preserves_empty_parent(self):
        """Normal agent step without parent context keeps parent fields empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "simple.yaml").write_text(yaml.dump({
                "name": "simple",
                "description": "Simple workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "step1", "agent": "genius", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                {"task": "genius", "workflow": "simple", "step": "step1"},
                tmpdir, ".agents/prompts",
            )
            assert result["PARENT_WORKFLOW"] == ""
            assert result["PARENT_WORKFLOW_STEP"] == ""

    def test_sub_workflow_terminal_with_conditions(self):
        """Terminal sub-workflow return-to-parent respects conditions on the sub-workflow step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "parent.yaml").write_text(yaml.dump({
                "name": "parent",
                "description": "Parent workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "start", "agent": "genius", "next": "subtask"},
                    {
                        "name": "subtask",
                        "workflow": "child",
                        "conditions": [
                            {"on_result": "approved", "next": "deploy"},
                            {"on_result": "rejected", "next": "fix"},
                            {"on_result": "default", "next": "deploy"},
                        ],
                    },
                    {"name": "deploy", "agent": "dev", "next": None},
                    {"name": "fix", "agent": "dev", "next": None},
                ],
            }))
            (Path(tmpdir) / "child.yaml").write_text(yaml.dump({
                "name": "child",
                "description": "Child workflow",
                "trigger": {"agents": ["genius"]},
                "steps": [
                    {"name": "review-step", "agent": "review", "next": None},
                ],
            }))

            resolve = self._import_resolver()

            # With branch="approved" → should go to "deploy"
            result = resolve(
                {
                    "task": "review",
                    "workflow": "child",
                    "step": "review-step",
                    "branch": "approved",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NAME"] == "parent"
            assert result["WORKFLOW_STEP"] == "deploy"

            # With branch="rejected" → should go to "fix"
            result = resolve(
                {
                    "task": "review",
                    "workflow": "child",
                    "step": "review-step",
                    "branch": "rejected",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NAME"] == "parent"
            assert result["WORKFLOW_STEP"] == "fix"

            # With no branch → falls back to linear next (None here), so workflow completes
            result = resolve(
                {
                    "task": "review",
                    "workflow": "child",
                    "step": "review-step",
                    "parent_workflow": "parent",
                    "parent_workflow_step": "subtask",
                },
                tmpdir, ".agents/prompts",
            )
            assert result["SKIP_AGENT"] == "true"  # No linear next, so terminal


# ── Spec-Driven Workflow Tests ───────────────────────────────────────


class TestSpecDrivenWorkflow:
    """Test spec-driven workflow resolution with conditional routing and skills."""

    def _import_resolver(self):
        import importlib
        import sys
        if "." not in sys.path:
            sys.path.insert(0, ".")
        resolve_workflow = importlib.import_module("resolve_workflow")
        return resolve_workflow.resolve_workflow_step

    def test_po_in_task_to_prompt(self):
        """Verify po agent is registered in TASK_TO_PROMPT."""
        import importlib
        import sys
        if "." not in sys.path:
            sys.path.insert(0, ".")
        resolve_workflow = importlib.import_module("resolve_workflow")
        assert "po" in resolve_workflow.TASK_TO_PROMPT
        assert resolve_workflow.TASK_TO_PROMPT["po"] == "po.md"

    def test_spec_step_resolves_po_agent(self):
        """The 'spec' step should resolve to the po agent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "spec-driven.yaml").write_text(yaml.dump({
                "name": "spec-driven",
                "description": "Spec-driven development",
                "trigger": {"agents": ["po"]},
                "steps": [
                    {"name": "spec", "agent": "po", "next": "spec_approval"},
                    {"name": "spec_approval", "agent": "genius", "next": "architect"},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                "po", "spec-driven", "spec", "", tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "po"
            assert result["WORKFLOW_NAME"] == "spec-driven"
            assert result["WORKFLOW_STEP"] == "spec"
            assert result["WORKFLOW_NEXT"] == "spec_approval"
            assert result["STEP_SKILLS"] == ""

    def test_spec_approval_with_approved_branch(self):
        """spec_approval with branch='approved' should route to architect."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "spec-driven.yaml").write_text(yaml.dump({
                "name": "spec-driven",
                "description": "Spec-driven development",
                "trigger": {"agents": ["po"]},
                "steps": [
                    {"name": "spec", "agent": "po", "next": "spec_approval"},
                    {
                        "name": "spec_approval",
                        "agent": "genius",
                        "conditions": [
                            {"on_result": "approved", "next": "architect"},
                            {"on_result": "rejected", "next": "spec"},
                            {"on_result": "default", "next": "architect"},
                        ],
                        "next": "architect",
                    },
                    {"name": "architect", "agent": "genius", "next": "stub"},
                    {"name": "stub", "agent": "dev", "skill": ["stub-only"], "next": "stub_review"},
                    {"name": "stub_review", "agent": "review", "next": "stub_approval"},
                    {
                        "name": "stub_approval",
                        "agent": "genius",
                        "conditions": [
                            {"on_result": "approved", "next": "implement"},
                            {"on_result": "rejected", "next": "stub"},
                            {"on_result": "default", "next": "implement"},
                        ],
                        "next": "implement",
                    },
                    {"name": "implement", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                "genius", "spec-driven", "spec_approval", "approved", tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "genius"
            assert result["WORKFLOW_NEXT"] == "architect"

    def test_spec_approval_with_rejected_branch(self):
        """spec_approval with branch='rejected' should route back to spec."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "spec-driven.yaml").write_text(yaml.dump({
                "name": "spec-driven",
                "description": "Spec-driven development",
                "trigger": {"agents": ["po"]},
                "steps": [
                    {"name": "spec", "agent": "po", "next": "spec_approval"},
                    {
                        "name": "spec_approval",
                        "agent": "genius",
                        "conditions": [
                            {"on_result": "approved", "next": "architect"},
                            {"on_result": "rejected", "next": "spec"},
                            {"on_result": "default", "next": "architect"},
                        ],
                        "next": "architect",
                    },
                    {"name": "architect", "agent": "genius", "next": "stub"},
                    {"name": "stub", "agent": "dev", "skill": ["stub-only"], "next": "stub_review"},
                    {"name": "stub_review", "agent": "review", "next": "stub_approval"},
                    {
                        "name": "stub_approval",
                        "agent": "genius",
                        "conditions": [
                            {"on_result": "approved", "next": "implement"},
                            {"on_result": "rejected", "next": "stub"},
                            {"on_result": "default", "next": "implement"},
                        ],
                        "next": "implement",
                    },
                    {"name": "implement", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                "genius", "spec-driven", "spec_approval", "rejected", tmpdir, ".agents/prompts",
            )
            assert result["WORKFLOW_NEXT"] == "spec"

    def test_stub_step_includes_stub_only_skill(self):
        """The stub step should inject the stub-only skill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "spec-driven.yaml").write_text(yaml.dump({
                "name": "spec-driven",
                "description": "Spec-driven development",
                "trigger": {"agents": ["po"]},
                "steps": [
                    {"name": "spec", "agent": "po", "next": "spec_approval"},
                    {"name": "spec_approval", "agent": "genius", "next": "architect"},
                    {"name": "architect", "agent": "genius", "next": "stub"},
                    {"name": "stub", "agent": "dev", "skill": ["stub-only"], "next": "stub_review"},
                    {"name": "stub_review", "agent": "review", "next": "stub_approval"},
                    {"name": "stub_approval", "agent": "genius", "next": "implement"},
                    {"name": "implement", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                "dev", "spec-driven", "stub", "", tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["STEP_SKILLS"] == "stub-only"

    def test_implement_step_has_no_skills(self):
        """The implement step should NOT have stub-only skill — full implementation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "spec-driven.yaml").write_text(yaml.dump({
                "name": "spec-driven",
                "description": "Spec-driven development",
                "trigger": {"agents": ["po"]},
                "steps": [
                    {"name": "spec", "agent": "po", "next": "spec_approval"},
                    {"name": "spec_approval", "agent": "genius", "next": "architect"},
                    {"name": "architect", "agent": "genius", "next": "stub"},
                    {"name": "stub", "agent": "dev", "skill": ["stub-only"], "next": "stub_review"},
                    {"name": "stub_review", "agent": "review", "next": "stub_approval"},
                    {"name": "stub_approval", "agent": "genius", "next": "implement"},
                    {"name": "implement", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                "dev", "spec-driven", "implement", "", tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["STEP_SKILLS"] == ""

    def test_stub_approval_with_default_branch_routes_to_implement(self):
        """stub_approval with no branch (default) should route to implement."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "spec-driven.yaml").write_text(yaml.dump({
                "name": "spec-driven",
                "description": "Spec-driven development",
                "trigger": {"agents": ["po"]},
                "steps": [
                    {"name": "spec", "agent": "po", "next": "spec_approval"},
                    {"name": "spec_approval", "agent": "genius", "next": "architect"},
                    {"name": "architect", "agent": "genius", "next": "stub"},
                    {"name": "stub", "agent": "dev", "skill": ["stub-only"], "next": "stub_review"},
                    {"name": "stub_review", "agent": "review", "next": "stub_approval"},
                    {
                        "name": "stub_approval",
                        "agent": "genius",
                        "conditions": [
                            {"on_result": "approved", "next": "implement"},
                            {"on_result": "rejected", "next": "stub"},
                            {"on_result": "default", "next": "implement"},
                        ],
                        "next": "implement",
                    },
                    {"name": "implement", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                "genius", "spec-driven", "stub_approval", "", tmpdir, ".agents/prompts",
            )
            assert result["WORKFLOW_NEXT"] == "implement"

    def test_implement_is_terminal_step(self):
        """The implement step has next=null, making it terminal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "spec-driven.yaml").write_text(yaml.dump({
                "name": "spec-driven",
                "description": "Spec-driven development",
                "trigger": {"agents": ["po"]},
                "steps": [
                    {"name": "spec", "agent": "po", "next": "spec_approval"},
                    {"name": "spec_approval", "agent": "genius", "next": "architect"},
                    {"name": "architect", "agent": "genius", "next": "stub"},
                    {"name": "stub", "agent": "dev", "skill": ["stub-only"], "next": "stub_review"},
                    {"name": "stub_review", "agent": "review", "next": "stub_approval"},
                    {"name": "stub_approval", "agent": "genius", "next": "implement"},
                    {"name": "implement", "agent": "dev", "next": None},
                ],
            }))

            resolve = self._import_resolver()
            result = resolve(
                "dev", "spec-driven", "implement", "", tmpdir, ".agents/prompts",
            )
            assert result["ROLE_NAME"] == "dev"
            assert result["WORKFLOW_NEXT"] == ""
