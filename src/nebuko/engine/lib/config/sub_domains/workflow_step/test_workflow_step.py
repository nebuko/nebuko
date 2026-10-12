"""Tests for WorkflowStep."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import WorkflowCondition, WorkflowStep


class TestWorkflowStepSubWorkflow:
    """Test WorkflowStep with sub-workflow support."""

    def test_step_with_workflow_only(self):
        step = WorkflowStep(name="subtask", workflow="review-only")
        assert step.workflow == "review-only"
        assert step.agent is None

    def test_step_with_workflow_and_path(self):
        step = WorkflowStep(name="subtask", workflow="cve", path=".custom/cve.yaml")
        assert step.workflow == "cve"
        assert step.path == ".custom/cve.yaml"

    def test_step_missing_both_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            WorkflowStep(name="broken", next="foo")

    def test_step_has_both_raises(self):
        with pytest.raises(ValueError, match="both"):
            WorkflowStep(name="bad", agent="dev", workflow="review-only")


class TestWorkflowStep:
    """Test WorkflowStep Pydantic model."""

    def test_simple_step(self):
        step = WorkflowStep(name="analyze", agent="architect", next="context")
        assert step.name == "analyze"
        assert step.agent == "architect"
        assert step.next == "context"
        assert step.conditions is None

    def test_terminal_step(self):
        step = WorkflowStep(name="review", agent="review", next=None)
        assert step.next is None

    def test_step_with_conditions(self):
        conditions = [
            WorkflowCondition(on_result="needs_review", next="review"),
            WorkflowCondition(on_result="needs_revision", next="dev"),
            WorkflowCondition(on_result="default", next=None),
        ]
        step = WorkflowStep(
            name="implement", agent="dev", conditions=conditions
        )
        assert len(step.conditions) == 3
        assert step.conditions[0].on_result == "needs_review"

    def test_step_with_skills(self):
        step = WorkflowStep(name="dev", agent="dev", skill=["python"])
        assert step.skill == ["python"]

    def test_step_with_multiple_skills(self):
        step = WorkflowStep(name="dev", agent="dev", skill=["python", "rust"])
        assert step.skill == ["python", "rust"]

    def test_step_without_skills_defaults_to_none(self):
        step = WorkflowStep(name="dev", agent="dev")
        assert step.skill is None

    def test_prompt_parses_plain_string(self):
        step = WorkflowStep(name="analyze", agent="architect", prompt="Be concise.")
        assert step.prompt == "Be concise."

    def test_prompt_parses_multiline_block_verbatim(self):
        step = WorkflowStep(
            name="analyze",
            agent="architect",
            prompt=(
                "Analyze the transcription and provide me:\n"
                "a short summary (~5-10 sentences)\n"
                "5 Key points\n"
            ),
        )
        assert step.prompt == (
            "Analyze the transcription and provide me:\n"
            "a short summary (~5-10 sentences)\n"
            "5 Key points\n"
        )

    def test_prompt_defaults_to_none(self):
        step = WorkflowStep(name="dev", agent="dev")
        assert step.prompt is None

    def test_prompt_coexists_with_existing_fields(self):
        step = WorkflowStep(
            name="stub",
            agent="dev",
            skill=["stub-only"],
            human_gate=True,
            next="stub_review",
            prompt="Fill in the stubs only.",
        )
        assert step.prompt == "Fill in the stubs only."
        assert step.skill == ["stub-only"]
        assert step.human_gate is True
        assert step.next == "stub_review"

    def test_human_gate_defaults_to_false(self):
        step = WorkflowStep(name="dev", agent="dev")
        assert step.human_gate is False

    def test_human_gate_can_be_true(self):
        step = WorkflowStep(name="spec_approval", agent="architect", human_gate=True)
        assert step.human_gate is True

    def test_human_gate_with_conditions(self):
        conditions = [
            WorkflowCondition(on_result="approved", next="implement"),
            WorkflowCondition(on_result="rejected", next="spec"),
        ]
        step = WorkflowStep(
            name="stub_approval",
            agent="architect",
            human_gate=True,
            conditions=conditions,
        )
        assert step.human_gate is True
        assert len(step.conditions) == 2

    def test_human_gate_step_with_next_and_conditions_is_valid(self):
        # Guard: a gated step may define both `next` and `conditions`.
        # `next` acts as fallback routing when the agent reports a branch no
        # condition matches (issue #506 review dispute over this combo).
        conditions = [
            WorkflowCondition(on_result="approved", next="implement"),
            WorkflowCondition(on_result="rejected", next="stub"),
        ]
        step = WorkflowStep(
            name="stub_approval",
            agent="architect",
            human_gate=True,
            conditions=conditions,
            next="implement",
        )
        assert step.human_gate is True
        assert step.next == "implement"
        assert len(step.conditions) == 2

    def test_prompt_replace_defaults_to_false(self):
        step = WorkflowStep(name="dev", agent="dev", prompt="Do the thing.")
        assert step.prompt_replace is False

    def test_prompt_replace_can_be_true(self):
        step = WorkflowStep(
            name="scaffold",
            agent="dev",
            prompt="Materialize the plan only.",
            prompt_replace=True,
        )
        assert step.prompt_replace is True
        assert step.prompt == "Materialize the plan only."


class TestWorkflowStepScript:
    """Test WorkflowStep with the deterministic script variant (issue #541)."""

    def test_minimal_script_step(self):
        step = WorkflowStep(name="run-tests", script={"path": "scripts/test.sh"})
        assert step.script.path == "scripts/test.sh"
        assert step.script.args is None
        assert step.script.timeout_minutes is None
        assert step.agent is None

    def test_script_step_with_args_and_timeout(self):
        step = WorkflowStep(
            name="run-tests",
            script={"path": "scripts/test.py", "args": ["--unit"], "timeout_minutes": 15},
        )
        assert step.script.args == ["--unit"]
        assert step.script.timeout_minutes == 15

    def test_script_step_with_next_routing(self):
        step = WorkflowStep(
            name="run-tests", script={"path": "scripts/test.sh"}, next="review"
        )
        assert step.next == "review"

    def test_script_requires_path(self):
        # A ``script`` source object must carry a repo-relative ``path``.
        with pytest.raises(ValueError, match="path"):
            WorkflowStep(name="broken", script={})

    def test_script_exclusive_of_agent(self):
        with pytest.raises(ValueError, match="cannot also declare"):
            WorkflowStep(
                name="bad", script={"path": "scripts/test.sh"}, agent="dev"
            )

    def test_script_exclusive_of_workflow(self):
        with pytest.raises(ValueError, match="cannot also declare"):
            WorkflowStep(
                name="bad",
                script={"path": "scripts/test.sh"},
                workflow="review-only",
            )

    def test_script_exclusive_of_repo(self):
        with pytest.raises(ValueError, match="cannot also declare"):
            WorkflowStep(
                name="bad",
                script={"path": "scripts/test.sh"},
                repo="https://example.com/repo.git",
                ref="main",
            )

    def test_script_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="repo-relative"):
            WorkflowStep(name="bad", script={"path": "/etc/passwd"})

    def test_script_traversal_path_rejected(self):
        with pytest.raises(ValueError, match="repo-relative"):
            WorkflowStep(name="bad", script={"path": "../outside.sh"})

    def test_script_nested_traversal_path_rejected(self):
        with pytest.raises(ValueError, match="repo-relative"):
            WorkflowStep(name="bad", script={"path": "scripts/../secrets/x.sh"})

    def test_script_nested_relative_path_allowed(self):
        step = WorkflowStep(name="ok", script={"path": "scripts/ci/test.sh"})
        assert step.script.path == "scripts/ci/test.sh"

    def test_script_dotdot_prefixed_filename_allowed(self):
        # '..' as a substring is fine; only '..' as a path segment is rejected.
        step = WorkflowStep(name="ok", script={"path": "scripts/..bar.sh"})
        assert step.script.path == "scripts/..bar.sh"

    def test_script_timeout_zero_rejected(self):
        # 0 would mean "no timeout" at runtime; only positive minutes are valid.
        with pytest.raises(ValueError, match="positive"):
            WorkflowStep(
                name="bad",
                script={"path": "scripts/test.sh", "timeout_minutes": 0},
            )

    def test_script_timeout_negative_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            WorkflowStep(
                name="bad",
                script={"path": "scripts/test.sh", "timeout_minutes": -5},
            )

    def test_script_path_field_rejected(self):
        # Root-level `path` belongs to workflow/repo steps, not script steps.
        with pytest.raises(ValueError, match="cannot declare 'path'"):
            WorkflowStep(
                name="bad",
                script={"path": "scripts/test.sh"},
                path="some/path",
            )

    def test_agent_step_with_script_source_rejected(self):
        # ``script`` is a competing source; it cannot coexist with ``agent``.
        with pytest.raises(ValueError, match="cannot also declare"):
            WorkflowStep(
                name="bad",
                agent="dev",
                script={"path": "scripts/test.sh"},
            )

    def test_existing_agent_step_unaffected(self):
        # Backward compatibility: plain agent steps parse exactly as before.
        step = WorkflowStep(name="analyze", agent="architect", next="context")
        assert step.script is None
        assert step.agent == "architect"


class TestWorkflowStepHooks:
    """Test the ``pre_hook``/``post_hook`` step fields (issue #884)."""

    def test_minimal_pre_hook(self):
        step = WorkflowStep(
            name="implement", agent="dev", pre_hook={"path": "scripts/init.sh"}
        )
        assert step.pre_hook.path == "scripts/init.sh"
        assert step.pre_hook.args is None
        assert step.pre_hook.timeout_minutes is None
        assert step.post_hook is None

    def test_minimal_post_hook(self):
        step = WorkflowStep(
            name="implement", agent="dev", post_hook={"path": "scripts/stats.sh"}
        )
        assert step.post_hook.path == "scripts/stats.sh"
        assert step.pre_hook is None

    def test_hook_args_and_timeout_round_trip(self):
        step = WorkflowStep(
            name="implement",
            agent="dev",
            pre_hook={
                "path": "scripts/init.sh",
                "args": ["--full", "x"],
                "timeout_minutes": 10,
            },
        )
        assert step.pre_hook.args == ["--full", "x"]
        assert step.pre_hook.timeout_minutes == 10

    def test_hook_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="repo-relative"):
            WorkflowStep(name="bad", agent="dev", pre_hook={"path": "/etc/passwd"})

    def test_hook_traversal_path_rejected(self):
        with pytest.raises(ValueError, match="repo-relative"):
            WorkflowStep(name="bad", agent="dev", post_hook={"path": "../outside.sh"})

    def test_hook_nested_traversal_path_rejected(self):
        with pytest.raises(ValueError, match="repo-relative"):
            WorkflowStep(
                name="bad", agent="dev", pre_hook={"path": "scripts/../secrets.sh"}
            )

    def test_hook_dotdot_prefixed_filename_allowed(self):
        step = WorkflowStep(
            name="ok", agent="dev", pre_hook={"path": "scripts/..bar.sh"}
        )
        assert step.pre_hook.path == "scripts/..bar.sh"

    def test_hook_timeout_zero_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            WorkflowStep(
                name="bad",
                agent="dev",
                pre_hook={"path": "scripts/init.sh", "timeout_minutes": 0},
            )

    def test_hook_requires_path(self):
        with pytest.raises(ValueError, match="path"):
            WorkflowStep(name="bad", agent="dev", pre_hook={})

    def test_hooks_rejected_on_script_step(self):
        with pytest.raises(ValueError, match="cannot declare 'pre_hook' or 'post_hook'"):
            WorkflowStep(
                name="bad",
                script={"path": "scripts/test.sh"},
                pre_hook={"path": "scripts/init.sh"},
            )

    def test_hooks_rejected_on_workflow_step(self):
        with pytest.raises(ValueError, match="only valid on an 'agent' step"):
            WorkflowStep(
                name="bad",
                workflow="review-only",
                post_hook={"path": "scripts/stats.sh"},
            )

    def test_hooks_rejected_on_repo_step(self):
        with pytest.raises(ValueError, match="only valid on an 'agent' step"):
            WorkflowStep(
                name="bad",
                repo="https://example.com/repo.git",
                ref="main",
                pre_hook={"path": "scripts/init.sh"},
            )

    def test_agent_step_with_hooks_has_no_other_source_side_effects(self):
        step = WorkflowStep(
            name="implement",
            agent="dev",
            pre_hook={"path": "scripts/init.sh"},
            post_hook={"path": "scripts/stats.sh"},
            next="review",
        )
        assert step.script is None
        assert step.workflow is None
        assert step.repo is None
        assert step.next == "review"

    def test_existing_agent_step_has_no_hooks(self):
        # Backward compatibility: a hookless step parses exactly as before.
        step = WorkflowStep(name="analyze", agent="architect", next="context")
        assert step.pre_hook is None
        assert step.post_hook is None


class TestWorkflowStepComplexity:
    """Test the per-step declared complexity tier (issue #787)."""

    def test_complexity_defaults_to_none(self):
        step = WorkflowStep(name="dev", agent="dev")
        assert step.complexity is None

    @pytest.mark.parametrize("tier", ["low", "medium", "high"])
    def test_complexity_accepts_valid_tiers(self, tier):
        step = WorkflowStep(name="dev", agent="dev", complexity=tier)
        assert step.complexity == tier

    def test_complexity_rejects_unknown_tier(self):
        with pytest.raises(ValueError):
            WorkflowStep(name="dev", agent="dev", complexity="ultra")

    def test_complexity_coexists_with_existing_fields(self):
        step = WorkflowStep(
            name="analyze",
            agent="architect",
            complexity="low",
            skill=["call-agent"],
            next="context",
        )
        assert step.complexity == "low"
        assert step.skill == ["call-agent"]
        assert step.next == "context"


class TestStepOutputsMigration:
    """The pre-#756 ``steps[].outputs`` key is rejected (issue #756)."""

    def test_legacy_outputs_key_is_rejected_with_a_hint(self):
        with pytest.raises(ValueError, match="#756"):
            WorkflowStep(name="plan", agent="architect", next=None, outputs=["doc"])

    def test_step_without_outputs_is_accepted(self):
        step = WorkflowStep(name="plan", agent="architect", next=None)
        assert step.prompt is None


class TestInputsListShorthand:
    def test_list_of_step_names_becomes_ref_bindings(self):
        step = WorkflowStep(
            name="review",
            agent="review",
            inputs=["plan", "implement"],
            next=None,
        )
        assert set(step.inputs) == {"plan", "implement"}
        assert step.inputs["plan"].ref == "plan"

    def test_prompt_field_round_trips(self):
        step = WorkflowStep(
            name="review", agent="review", prompt="see {{ plan }}", next=None
        )
        assert step.prompt == "see {{ plan }}"
