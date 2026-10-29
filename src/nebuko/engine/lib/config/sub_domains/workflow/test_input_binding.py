"""Tests for the typed workflow-input schema (issue #813).

A workflow is a typed function: ``WorkflowInputSpec`` declares a parameter and
``InputBinding`` supplies a value at the call site. These tests pin the
field-level contract — exactly-one-source bindings, the bare-string ``ref``
shorthand, the reference grammar, and the grammar extensions on the workflow
definition and its steps.
"""

import os
import sys
from pathlib import Path

import pytest

from nebuko.engine.lib.config import (
    InputBinding,
    InputMaterializationError,
    WorkflowDefinition,
    WorkflowInputSpec,
    WorkflowStep,
)


class TestInputBindingSchema:
    def test_bare_string_is_a_ref_shorthand(self):
        binding = InputBinding.model_validate("input.diff")
        assert binding.source == "ref"
        assert binding.ref == "input.diff"

    def test_exec_binding_carries_an_argv_list(self):
        binding = InputBinding.model_validate({"exec": ["git", "diff"]})
        assert binding.source == "exec"
        assert binding.exec_ == ["git", "diff"]

    def test_str_binding_carries_a_constant(self):
        binding = InputBinding.model_validate({"str": "nebuko"})
        assert binding.source == "str"
        assert binding.str_ == "nebuko"

    def test_block_style_keys_round_trip_through_dump(self):
        binding = InputBinding.model_validate({"exec": ["echo", "hi"]})
        assert binding.model_dump(by_alias=True, exclude_none=True) == {
            "exec": ["echo", "hi"]
        }

    def test_zero_source_keys_are_rejected(self):
        with pytest.raises(ValueError, match="exactly one"):
            InputBinding.model_validate({})

    def test_two_source_keys_are_rejected(self):
        with pytest.raises(ValueError, match="exactly one"):
            InputBinding.model_validate({"exec": ["true"], "str": "x"})

    def test_ref_must_match_the_reference_grammar(self):
        with pytest.raises(ValueError, match="grammar"):
            InputBinding.model_validate({"ref": "9lives!"})

    def test_bare_step_name_is_accepted(self):
        binding = InputBinding.model_validate("plan")
        assert binding.source == "ref"
        assert binding.ref == "plan"

    def test_dotted_step_output_reference_is_accepted(self):
        binding = InputBinding.model_validate("spec.patch_ready")
        assert binding.source == "ref"
        assert binding.ref == "spec.patch_ready"

    def test_malformed_dotted_reference_is_rejected(self):
        with pytest.raises(ValueError, match="grammar"):
            InputBinding.model_validate("spec.Patch-Ready")


class TestInputBindingResolve:
    def test_str_resolves_to_its_constant(self):
        binding = InputBinding.model_validate({"str": "value"})
        assert binding.resolve({}) == "value"

    def test_ref_resolves_through_the_scope(self):
        binding = InputBinding.model_validate("input.setup")
        assert binding.resolve({"input.setup": "ready"}) == "ready"

    def test_unresolved_ref_raises_typed_error(self):
        binding = InputBinding.model_validate("input.missing")
        with pytest.raises(InputMaterializationError, match="unresolved"):
            binding.resolve({})

    def test_exec_captures_stdout(self):
        binding = InputBinding.model_validate({"exec": ["echo", "captured"]})
        assert binding.resolve({}) == "captured\n"

    def test_exec_non_zero_exit_raises_typed_error(self):
        binding = InputBinding.model_validate(
            {"exec": ["sh", "-c", "echo boom >&2; exit 3"]}
        )
        with pytest.raises(InputMaterializationError, match="code 3"):
            binding.resolve({})


class TestWorkflowInputSpec:
    def test_kind_is_restricted_to_exec_or_str(self):
        with pytest.raises(ValueError):
            WorkflowInputSpec(name="x", kind="int")

    def test_required_defaults_to_true(self):
        assert WorkflowInputSpec(name="x", kind="str").required is True

    def test_matching_kind_materializes(self):
        spec = WorkflowInputSpec(name="x", kind="str")
        assert spec.materialize(InputBinding.model_validate({"str": "v"}), {}) == "v"

    def test_contradicting_literal_kind_is_rejected(self):
        spec = WorkflowInputSpec(name="x", kind="exec")
        with pytest.raises(InputMaterializationError, match="expects a 'exec'"):
            spec.materialize(InputBinding.model_validate({"str": "v"}), {})

    def test_ref_binding_is_kind_preserving(self):
        """A ``ref`` is allowed regardless of kind — the linter checks the chain."""
        spec = WorkflowInputSpec(name="x", kind="exec")
        binding = InputBinding.model_validate("input.x")
        assert spec.materialize(binding, {"input.x": "argv"}) == "argv"


def _definition(**overrides) -> WorkflowDefinition:
    values = {
        "name": "wf",
        "description": "d",
        "steps": [WorkflowStep(name="s", agent="dev")],
    }
    values.update(overrides)
    return WorkflowDefinition(**values)


class TestGrammarExtensions:
    def test_workflow_declares_typed_inputs(self):
        definition = _definition(
            inputs=[{"name": "diff", "kind": "exec"}],
            outputs=["patch_ready"],
        )
        assert definition.inputs[0].name == "diff"
        assert definition.outputs == ["patch_ready"]

    def test_bare_string_output_coerces_to_a_list(self):
        assert _definition(outputs="patch_ready").outputs == ["patch_ready"]

    def test_inputs_and_outputs_default_to_empty(self):
        definition = _definition()
        assert definition.inputs == []
        assert definition.outputs == []

    def test_step_wires_inputs_and_prompt(self):
        step = WorkflowStep(
            name="inspect",
            agent="dev",
            inputs={"diff": "input.diff"},
            prompt="Review {{ plan }}",
        )
        assert step.inputs["diff"].ref == "input.diff"
        assert step.prompt == "Review {{ plan }}"

    def test_step_inputs_list_shorthand_names_upstream_steps(self):
        step = WorkflowStep(name="review", agent="dev", inputs=["plan", "implement"])
        assert step.inputs["plan"].ref == "plan"
        assert step.inputs["implement"].ref == "implement"

    def test_step_inputs_default_to_none(self):
        assert WorkflowStep(name="s", agent="dev").inputs is None
