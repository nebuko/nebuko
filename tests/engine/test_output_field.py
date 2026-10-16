"""Tests for the declared step-output DSL (issue #973)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import OutputField, StepFieldRef, StepOutput, WorkflowStep


class TestOutputFieldValidation:
    def test_name_pattern_rejects_dot_and_space(self):
        for bad in ("open.questions", "open questions", "Open", "1st", "a-->b"):
            with pytest.raises(ValidationError):
                OutputField(name=bad)

    def test_name_pattern_accepts_snake_case(self):
        assert OutputField(name="open_questions").name == "open_questions"

    def test_table_requires_columns(self):
        with pytest.raises(ValidationError, match="requires 'columns'"):
            OutputField(name="rows", type="table")

    def test_columns_only_on_table(self):
        with pytest.raises(ValidationError, match="only valid on a 'table'"):
            OutputField(name="body", columns=["a"])

    def test_column_names_use_the_same_pattern(self):
        with pytest.raises(ValidationError, match="must match"):
            OutputField(name="rows", type="table", columns=["Bad Name"])

    def test_columns_must_be_unique(self):
        with pytest.raises(ValidationError, match="unique"):
            OutputField(name="rows", type="table", columns=["a", "a"])

    def test_title_requires_text(self):
        with pytest.raises(ValidationError, match="only valid on a 'text'"):
            OutputField(name="rows", type="table", columns=["a"], title=True)

    def test_rendered_heading_defaults_to_capitalized_name(self):
        assert OutputField(name="implementation").rendered_heading == "Implementation"

    def test_rendered_heading_honours_explicit_heading(self):
        field = OutputField(name="implementation", heading="Dev steps")
        assert field.rendered_heading == "Dev steps"

    def test_required_defaults_true(self):
        assert OutputField(name="body").required is True


class TestStepOutputValidation:
    def test_field_names_must_be_unique(self):
        with pytest.raises(ValidationError, match="unique"):
            StepOutput(fields=[OutputField(name="a"), OutputField(name="a")])

    def test_at_most_one_title(self):
        with pytest.raises(ValidationError, match="at most one"):
            StepOutput(
                fields=[
                    OutputField(name="a", title=True),
                    OutputField(name="b", title=True),
                ]
            )

    def test_reserved_names_are_legal(self):
        # User fields live in the text answer, not the tool's top-level args,
        # so a name like ``summary`` cannot collide (fix 4, issue #973).
        output = StepOutput(fields=[OutputField(name="summary")])
        assert output.fields[0].name == "summary"


class TestWorkflowStepOutput:
    def test_step_parses_output_declaration(self):
        step = WorkflowStep(
            name="plan",
            agent="architect",
            output={
                "fields": [
                    {"name": "headline", "title": True},
                    {"name": "implementation", "collapsed": True},
                    {"name": "questions", "required": False},
                ]
            },
        )
        assert [field.name for field in step.output.fields] == [
            "headline",
            "implementation",
            "questions",
        ]
        assert step.output.fields[2].required is False

    def test_step_without_output_is_none(self):
        assert WorkflowStep(name="plan", agent="architect").output is None

    def test_step_rejects_bad_output_declaration(self):
        with pytest.raises(ValidationError):
            WorkflowStep(
                name="plan",
                agent="architect",
                output={"fields": [{"name": "Bad Name"}]},
            )


class TestStepFieldRef:
    """The shared ``<step>`` / ``<step>.<field>`` parser (issue #973)."""

    def test_bare_step_name_is_not_a_field_ref(self):
        ref = StepFieldRef.parse("plan")
        assert (ref.step, ref.field) == ("plan", None)
        assert ref.is_field_ref is False

    def test_dotted_ref_splits_on_the_first_dot(self):
        ref = StepFieldRef.parse("plan.headline")
        assert (ref.step, ref.field) == ("plan", "headline")
        assert ref.is_field_ref is True

    def test_malformed_parts_are_rejected(self):
        for bad in ("9lives", "plan.Bad", "plan.head-line", "plan.", ".headline"):
            with pytest.raises(ValueError):
                StepFieldRef.parse(bad)
