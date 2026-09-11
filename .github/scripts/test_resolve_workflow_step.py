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


class TestSubWorkflowEntry:
    """Test sub-workflow entry scenarios."""

    @pytest.fixture
    def sub_wf_workflows_dir(self, tmp_path: Path) -> Path:
        """Create workflows where parent has a step referencing a child workflow."""
        # Parent workflow — step "subtask" references child workflow
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent with sub-workflow",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "start", "agent": "genius", "next": "subtask"},
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
            "trigger": {"agents": ["dev"]},
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
        assert result == {
            "entered_sub_workflow": True,
            "task": "dev",
            "step": "child_start",
            "workflow": "child",
            "parent_workflow": "parent",
            "parent_workflow_step": "subtask",
        }

    def test_sub_workflow_first_step_resolution(self, sub_wf_workflows_dir):
        """Resolving from the first step of a child workflow works normally."""
        resolve = _import_resolver()
        result = resolve(
            "child", "child_start", str(sub_wf_workflows_dir),
        )
        assert result == {"task": "review", "step": "child_end"}


class TestSubWorkflowTraversal:
    """Integration tests for full sub-workflow entry/exit traversal."""

    @pytest.fixture
    def nested_dirs(self, tmp_path: Path) -> tuple[Path, Path]:
        """Parent + child workflows used for end-to-end traversal."""
        (tmp_path / "parent.yaml").write_text(yaml.dump({
            "name": "parent",
            "description": "Parent with sub-workflow",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "analyze", "agent": "genius", "next": "subtask"},
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
            "trigger": {"agents": ["dev"]},
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

        # 1. Parent's first step advances to the sub-workflow reference
        #    (a reference step carries no agent of its own)
        assert resolve("parent", "analyze", str(wf_dir)) == {
            "task": None, "step": "subtask",
        }

        # 2. Sub-workflow reference step yields an entry marker
        entry = resolve("parent", "subtask", str(wf_dir))
        assert entry == {
            "entered_sub_workflow": True,
            "task": "dev",
            "step": "impl",
            "workflow": "child",
            "parent_workflow": "parent",
            "parent_workflow_step": "subtask",
        }

        # 3. Inside the child: normal linear resolution
        r = resolve(
            "child", "impl", str(wf_dir),
            parent_workflow="parent", parent_workflow_step="subtask",
        )
        assert r == {"task": "review", "step": "verify"}

        # 4. Child terminal step exits back to the parent's next step
        r = resolve(
            "child", "verify", str(wf_dir),
            parent_workflow="parent", parent_workflow_step="subtask",
        )
        assert r == {
            "exited_sub_workflow": True,
            "task": "review",
            "step": "finish",
            "workflow": "parent",
        }

        # 5. Parent terminal (no sub-workflow context): overall terminal
        r = resolve("parent", "finish", str(wf_dir))
        assert r is None

    def test_child_loop_stays_within_sub_workflow(self, tmp_path: Path):
        """A rejected-review loop inside the child never escapes the child."""
        (tmp_path / "loop_parent.yaml").write_text(yaml.dump({
            "name": "loop_parent",
            "description": "Parent of looping child",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "start", "agent": "genius", "next": "run"},
                {"name": "run", "workflow": "loop_child", "next": "done"},
                {"name": "done", "agent": "review", "next": None},
            ],
        }))
        (tmp_path / "loop_child.yaml").write_text(yaml.dump({
            "name": "loop_child",
            "description": "Child with a rejection loop",
            "trigger": {"agents": ["dev"]},
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
        assert resolve("loop_child", "implement", str(tmp_path), **parent_ctx) == {
            "task": "review", "step": "review",
        }
        # rejected → loops back inside the child (no parent escape)
        assert resolve(
            "loop_child", "review", str(tmp_path), branch_result="rejected", **parent_ctx,
        ) == {"task": "dev", "step": "implement", "branch": "rejected"}
        # approved → child terminal → exit to parent's step after the ref
        exit_r = resolve(
            "loop_child", "review", str(tmp_path), branch_result="approved", **parent_ctx,
        )
        assert exit_r == {
            "exited_sub_workflow": True,
            "task": "review",
            "step": "done",
            "workflow": "loop_parent",
        }

    def test_terminal_without_parent_returns_none(self, nested_dirs):
        """Child terminal with no parent context is overall terminal."""
        wf_dir, _ = nested_dirs
        resolve = _import_resolver()
        assert resolve("child", "verify", str(wf_dir)) is None

    def test_path_based_workflow_reference(self, tmp_path: Path, monkeypatch):
        """resolve_next_from_workflow accepts a repo-root-relative path."""
        monkeypatch.chdir(tmp_path)
        subdir = tmp_path / "tmp_impl"
        subdir.mkdir()
        (subdir / "flow.yaml").write_text(yaml.dump({
            "name": "flow",
            "description": "Path-resolved workflow",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "one", "agent": "dev", "next": "two"},
                {"name": "two", "agent": "review", "next": None},
            ],
        }))
        resolve = _import_resolver()
        assert resolve("tmp_impl/flow.yaml", "one", str(tmp_path)) == {
            "task": "review",
            "step": "two",
        }


class TestCircularReference:
    """Test circular reference detection in step transitions."""

    @pytest.fixture
    def circular_workflows_dir(self, tmp_path: Path) -> Path:
        """Create a workflow where step A → B → A (circular)."""
        (tmp_path / "circular.yaml").write_text(yaml.dump({
            "name": "circular",
            "description": "Circular reference workflow",
            "trigger": {"agents": ["genius"]},
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
        assert result == {"task": "review", "step": "step_b"}

    def test_circular_reference_reverse(self, circular_workflows_dir):
        """Resolving from step_b → step_a also works."""
        resolve = _import_resolver()
        result = resolve(
            "circular", "step_b", str(circular_workflows_dir),
        )
        assert result == {"task": "dev", "step": "step_a"}


class TestMissingNextStep:
    """Test missing next step reference in YAML."""

    @pytest.fixture
    def broken_next_workflows_dir(self, tmp_path: Path) -> Path:
        """Create a workflow where step.next points to a non-existent step name."""
        (tmp_path / "broken_next.yaml").write_text(yaml.dump({
            "name": "broken_next",
            "description": "Broken next step reference",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "start", "agent": "genius", "next": "ghost"},
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
        assert result is None

    def test_missing_next_step_with_hint(self, broken_next_workflows_dir):
        """When step.next references a non-existent step, use agent_hint."""
        resolve = _import_resolver()
        result = resolve(
            "broken_next", "start", str(broken_next_workflows_dir),
            agent_hint="dev",
        )
        # agent_hint only applies for terminal/missing-current-step; not for broken next
        # The current step exists and is not terminal, so it proceeds to look up the target.
        # Target "ghost" doesn't exist → returns None (no hint fallback at this stage)
        assert result is None


class TestConditionsOnlyWorkflow:
    """Test workflow with only conditions and no default next."""

    @pytest.fixture
    def conditions_only_workflows_dir(self, tmp_path: Path) -> Path:
        """Create a workflow where a step has conditions but no default next."""
        (tmp_path / "conditions_only.yaml").write_text(yaml.dump({
            "name": "conditions_only",
            "description": "Workflow with conditions-only step",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {"name": "start", "agent": "genius", "next": "decision"},
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
        assert result == {"task": "dev", "step": "proceed", "branch": "approved"}

    def test_conditions_only_no_branch_uses_linear_next(self, conditions_only_workflows_dir):
        """Without branch_result and a null 'next', the engine backstops to a
        retry of the same step with a diagnostic (issue #569) instead of
        resolving silently to terminal."""
        resolve = _import_resolver()
        result = resolve(
            "conditions_only", "decision", str(conditions_only_workflows_dir),
        )
        assert result is not None
        assert result["task"] == "review"
        assert result["step"] == "decision"
        assert "carried no branch" in result["reason"]
        assert "'approved'" in result["reason"] and "'rejected'" in result["reason"]

    def test_conditions_only_no_default_fallback(self, conditions_only_workflows_dir):
        """Unmatched branch with no default condition returns None."""
        resolve = _import_resolver()
        result = resolve(
            "conditions_only", "decision", str(conditions_only_workflows_dir),
            branch_result="unknown",
        )
        # No matching condition, no "default" condition → None
        assert result is None


class TestConditionBackstop:
    """Backstop routing when conditions exist but no branch is emitted (issue #569)."""

    def test_backstop_not_applied_when_default_exists(self, tmp_path: Path):
        """With no branch and a null linear 'next', resolution falls through
        to terminal even when a 'default' condition exists — matching the
        pre-existing pinned behavior (default fallback only applies to a
        present-but-unmatched branch)."""
        (tmp_path / "wf.yaml").write_text(yaml.dump({
            "name": "wf",
            "description": "Workflow with default condition",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {
                    "name": "decision",
                    "agent": "genius",
                    "next": None,
                    "conditions": [
                        {"on_result": "approved", "next": "done"},
                        {"on_result": "default", "next": "done"},
                    ],
                },
                {"name": "done", "agent": "dev", "next": None},
            ],
        }))
        resolve = _import_resolver()
        # No branch, no linear next → terminal (no backstop: a default exists)
        assert resolve("wf", "decision", str(tmp_path)) is None
        # A present-but-unmatched branch still routes via the default condition
        assert resolve(
            "wf", "decision", str(tmp_path), branch_result="bogus"
        ) == {"task": "dev", "step": "done", "branch": "default"}

    def test_backstop_not_applied_when_linear_next_exists(self, tmp_path: Path):
        """A non-null linear 'next' field resolves normally — no backstop."""
        (tmp_path / "wf.yaml").write_text(yaml.dump({
            "name": "wf",
            "description": "Workflow with conditions and linear next",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {
                    "name": "decision",
                    "agent": "genius",
                    "next": "fallback",
                    "conditions": [
                        {"on_result": "approved", "next": "done"},
                    ],
                },
                {"name": "fallback", "agent": "dev", "next": None},
                {"name": "done", "agent": "dev", "next": None},
            ],
        }))
        resolve = _import_resolver()
        result = resolve("wf", "decision", str(tmp_path))
        assert result == {"task": "dev", "step": "fallback"}

    def test_unmatched_branch_without_default_is_terminal(self, tmp_path: Path):
        """A present-but-unmatched branch keeps the legacy fall-through
        (linear next null → terminal); the backstop only applies when the
        dispatch carried no branch at all."""
        (tmp_path / "wf.yaml").write_text(yaml.dump({
            "name": "wf",
            "description": "Workflow with conditions, no default, no next",
            "trigger": {"agents": ["genius"]},
            "steps": [
                {
                    "name": "decision",
                    "agent": "genius",
                    "next": None,
                    "conditions": [
                        {"on_result": "approved", "next": "done"},
                    ],
                },
                {"name": "done", "agent": "dev", "next": None},
            ],
        }))
        resolve = _import_resolver()
        result = resolve("wf", "decision", str(tmp_path), branch_result="bogus")
        assert result is None


class TestEmptyWorkflowDefinition:
    """Test empty workflow definition."""

    @pytest.fixture
    def empty_workflows_dir(self, tmp_path: Path) -> Path:
        """Create a workflow with no steps."""
        (tmp_path / "empty.yaml").write_text(yaml.dump({
            "name": "empty",
            "description": "Empty workflow",
            "trigger": {"agents": ["genius"]},
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
        assert result == {"task": "dev"}


def _real_workflows_dir() -> Path:
    """Return the path to the real workflows/ directory in the repo root."""
    return Path(__file__).resolve().parents[2] / "workflows"


class TestDefaultWorkflowWithRealFile:
    """Test resolve_next_from_workflow against the real workflows/default.yaml.

    Regression test for issue #377 — ensures dispatch.advance from the
    'analyze' step correctly resolves to the 'ctx' agent.
    """

    def test_analyze_resolves_to_ctx(self):
        """dispatch.advance from analyze step → ctx agent (context step)."""
        resolve = _import_resolver()
        result = resolve("default", "analyze", str(_real_workflows_dir()))
        assert result == {"task": "ctx", "step": "context"}

    def test_context_resolves_to_genius_plan(self):
        """dispatch.advance from context step → genius agent (plan step)."""
        resolve = _import_resolver()
        result = resolve("default", "context", str(_real_workflows_dir()))
        assert result == {"task": "genius", "step": "plan"}

    def test_plan_resolves_to_dev_implement(self):
        """dispatch.advance from plan step → dev agent (implement step)."""
        resolve = _import_resolver()
        result = resolve("default", "plan", str(_real_workflows_dir()))
        assert result == {"task": "dev", "step": "implement"}

    def test_implement_resolves_to_review(self):
        """dispatch.advance from implement step → review agent."""
        resolve = _import_resolver()
        result = resolve("default", "implement", str(_real_workflows_dir()))
        assert result == {"task": "review", "step": "review"}

    def test_review_is_terminal_returns_none(self):
        """Review is the terminal step — returns None."""
        resolve = _import_resolver()
        result = resolve("default", "review", str(_real_workflows_dir()))
        assert result is None

    def test_body_from_issue_resolves_to_ctx(self):
        """End-to-end test: parse the exact body text from issue #377 and verify ctx is resolved.

        The genius agent finishes the 'analyze' step with dispatch.advance (no task field).
        The body contains a WORKFLOW_STATE block. The resolver should extract the state,
        find the next step in default.yaml ('context'), and return agent 'ctx'.
        """
        resolve = _import_resolver()

        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from workflow_state.features.get_last_state.get_last_state import extract_state_from_body

        body = (
            "<summary>🤖 Workflow State (v1)</summary>\n"
            "\n"
            "```json\n"
            "<!-- WORKFLOW_STATE_START -->\n"
            '{\n'
            '  "workflow": "default",\n'
            '  "step": "analyze",\n'
            '  "agent": "genius",\n'
            '  "phase": "analysis",\n'
            '  "history": [\n'
            '    {\n'
            '      "step": "analyze",\n'
            '      "agent": "genius",\n'
            '      "summary": "Genius answer summary"\n'
            "    }\n"
            "  ],\n"
            '  "metrics": {\n'
            '    "run_id": "d17d26d4-c4de-4808-b80b-e353d79e0563",\n'
            '    "model_name": "default",\n'
            '    "agent_role": "genius",\n'
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
        assert state.agent == "genius"

        result = resolve(state.workflow, state.step, str(_real_workflows_dir()))
        assert result == {"task": "ctx", "step": "context"}
