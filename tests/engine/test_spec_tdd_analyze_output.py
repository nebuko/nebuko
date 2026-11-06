"""Spec tests: spec-tdd analyze output must carry the new analyze rules.

Commit 0e863dd rewrote the analyze step prompt to require candidate
approaches, verification claims (``agent_call.verify``), complexity
estimation and a typed hand-off — but the step's ``output.fields`` still
declares only headline/context_request/assumptions, so the posted report
cannot carry the new artifacts (issue #1282).

RED against the current workflow: the tests fail until the analyze
``output.fields`` block, the stale prompt line and the context step prompt
land in ``.nebuko/workflows/spec-tdd.yaml``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from nebuko.engine.lib.config import WorkflowDefinition

REPO = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO / ".nebuko" / "workflows" / "spec-tdd.yaml"


def _definition() -> WorkflowDefinition:
    raw = yaml.safe_load(WORKFLOW_PATH.read_text())
    return WorkflowDefinition.model_validate(raw)


def _step(name: str):
    return next(step for step in _definition().steps if step.name == name)


class TestTheAnalyzeStepOutputDeclaration:
    def test_then_it_declares_its_fields_in_contract_order(self):
        fields = _step("analyze").output.fields
        assert [f.name for f in fields] == [
            "headline",
            "approaches",
            "claims",
            "context_request",
            "assumptions",
            "requirements",
        ]

    def test_then_headline_is_the_title_field(self):
        headline = _step("analyze").output.fields[0]
        assert headline.title is True
        assert headline.type == "text"

    def test_then_approaches_is_a_collapsed_table_with_contract_columns(self):
        approaches = next(
            f for f in _step("analyze").output.fields if f.name == "approaches"
        )
        assert approaches.type == "table"
        assert approaches.collapsed is True
        assert approaches.columns == ["approach", "dependencies", "verdict"]

    def test_then_claims_is_an_optional_collapsed_table_mirroring_verify(self):
        claims = next(
            f for f in _step("analyze").output.fields if f.name == "claims"
        )
        assert claims.type == "table"
        assert claims.required is False
        assert claims.collapsed is True
        assert claims.columns == ["id", "claim", "kind", "category", "if_wrong"]

    def test_then_context_request_stays_required_and_collapsed(self):
        context_request = next(
            f for f in _step("analyze").output.fields if f.name == "context_request"
        )
        assert context_request.required is True
        assert context_request.collapsed is True

    def test_then_assumptions_stays_optional(self):
        assumptions = next(
            f for f in _step("analyze").output.fields if f.name == "assumptions"
        )
        assert assumptions.required is False


class TestTheAnalyzeStepPrompt:
    def test_then_the_closing_turn_line_references_the_declared_sections(self):
        prompt = _step("analyze").prompt
        assert (
            "the engine's closing turn asks only for this step's report in its "
            "declared sections" in prompt.lower()
        )
        assert "(the context request)" not in prompt


class TestTheContextStep:
    def test_then_it_declares_no_output_block(self):
        assert _step("context").output is None

    def test_then_its_prompt_documents_the_claims_contract(self):
        prompt = _step("context").prompt
        assert "agent_call.task" in prompt
        assert "verify" in prompt
        assert "claim" in prompt.lower()


class TestTheDeclarationIsTheEngineSchema:
    def test_then_the_workflow_still_validates_against_the_engine_dsl(self):
        # A malformed declaration (bad column name, two titles, duplicate
        # fields) must fail at load time, not degrade at finalize.
        with pytest.raises(ValidationError):
            WorkflowDefinition.model_validate(
                {
                    "name": "spec-tdd",
                    "description": "d",
                    "steps": [
                        {
                            "name": "analyze",
                            "agent": "architect",
                            "next": "context",
                            "output": {
                                "fields": [
                                    {"name": "headline", "title": True},
                                    {
                                        "name": "claims",
                                        "type": "table",
                                        "columns": ["Bad Column"],
                                    },
                                ]
                            },
                        }
                    ],
                }
            )
