"""The plan step receives analyze's requirements and must cover their IDs.

The fixtures are the real tables from #1322: analyze wrote a four-column ledger
with no kind column, which parsed to nothing, so the coverage check passed
without testing anything while the plan renumbered the requirements.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.context_selection import ContextInput, ContextKind, WorkflowPathEntry
from nebuko.engine.finalize import SECTION_CLOSE, SECTION_OPEN
from nebuko.engine.input_wiring import StepInputs, materialize, render
from nebuko.engine.ledger import (
    CoverageTable,
    CoverageValidationError,
    LedgerKind,
    PlanCoverageGuard,
    RequirementsLedger,
)
from nebuko.engine.lib.config import load_workflow
from nebuko.engine.lib.resolve_workflow import build_step_inputs
from nebuko.engine.models import Trigger
from nebuko.engine.paths import repo_root
from nebuko.engine.run_loop import Engine

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "ledger_fixtures"
WORKFLOWS = (
    "defaults/workflows/default.yaml",
    ".nebuko/workflows/default-with-output.yaml",
    ".nebuko/workflows/spec-tdd.yaml",
)
ENFORCED = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "K1"]
FULL_COVERAGE = "\n".join(
    ["| ID | Covered by | Verified by |", "|---|---|---|"]
    + [f"| {ledger_id} | a plan step | a test |" for ledger_id in ENFORCED]
)


def _requirements() -> str:
    return (FIXTURES / "issue_1322_requirements.md").read_text()


def _renumbered_coverage() -> str:
    return (FIXTURES / "issue_1322_coverage.md").read_text()


def _step(workflow: str, name: str):
    definition = load_workflow(str(ROOT / workflow))
    return definition, next(step for step in definition.steps if step.name == name)


def _engine(tmp_path) -> Engine:
    trigger = Trigger(
        author="olivier-norture",
        body="/nebuko resume",
        type="issue_comment",
        number=1322,
        issue_body="",
    )
    return Engine(
        adapter=LocalAdapter(tmp_path / "runs" / "r1", trigger=trigger),
        repo_root=repo_root(),
        env={"GITHUB_REPOSITORY": "nebuko/nebuko"},
    )


def _section(name: str, body: str) -> str:
    return "\n".join([SECTION_OPEN.format(name=name), body, SECTION_CLOSE])


class TestLedgerParsing:
    def test_the_real_four_column_ledger_parses(self):
        ledger = RequirementsLedger.parse(_requirements())

        assert ledger.ids() == [*ENFORCED[:-1], "NG1", "K1", "Q1"]
        assert ledger.enforced_ids() == ENFORCED

    def test_the_kind_comes_from_the_id_prefix(self):
        kinds = {e.id: e.kind for e in RequirementsLedger.parse(_requirements()).entries}

        assert kinds["R1"] is LedgerKind.REQUIREMENT
        assert kinds["K1"] is LedgerKind.CONSTRAINT
        assert kinds["NG1"] is LedgerKind.NON_GOAL

    def test_a_login_in_the_origin_column_reads_as_explicit(self):
        ledger = RequirementsLedger.parse(_requirements())

        assert ledger.inferred_ids() == ["NG1"]  # "olivier-norture (derived)"

    def test_a_pipe_in_a_statement_keeps_the_row(self):
        ledger = RequirementsLedger.parse("| R1 | keep a | b together | issue | explicit |\n")

        assert ledger.entries[0].statement == "keep a | b together"
        assert ledger.entries[0].source == "issue"

    def test_a_declared_kind_must_still_match_the_id(self):
        with pytest.raises(ValueError):
            RequirementsLedger.parse("| R1 | constraint | statement | issue | explicit |\n")


class TestCoverageGuard:
    def test_the_real_renumbered_plan_is_refused(self):
        with pytest.raises(CoverageValidationError) as refused:
            PlanCoverageGuard().check(_renumbered_coverage(), _requirements())

        assert sorted(refused.value.missing_ids) == sorted(ENFORCED)

    def test_a_row_mixing_the_id_and_a_description_does_not_count(self):
        assert CoverageTable.parse(_renumbered_coverage()).covered_ids() == set()

    def test_a_plan_covering_every_id_passes(self):
        PlanCoverageGuard().check(FULL_COVERAGE, _requirements())

    def test_one_missing_id_is_named(self):
        coverage = FULL_COVERAGE.replace("| K1 | a plan step | a test |\n", "").replace(
            "| K1 | a plan step | a test |", ""
        )

        with pytest.raises(CoverageValidationError) as refused:
            PlanCoverageGuard().check(coverage, _requirements())

        assert refused.value.missing_ids == ["K1"]

    def test_a_ledger_with_no_rows_warns_instead_of_passing_silently(self, caplog):
        with caplog.at_level(logging.WARNING, logger="nebuko.engine.ledger"):
            PlanCoverageGuard().check(FULL_COVERAGE, "prose, not a table")

        assert "no ledger rows" in caplog.text

    def test_a_missing_ledger_stays_silent(self, caplog):
        with caplog.at_level(logging.WARNING, logger="nebuko.engine.ledger"):
            PlanCoverageGuard().check(FULL_COVERAGE, None)

        assert caplog.text == ""


class TestCheckOnlyBindsThePlanStep:
    def _check(self, tmp_path, monkeypatch, workflow: str, step_name: str, coverage: str):
        _, step = _step(workflow, step_name)
        engine = _engine(tmp_path)
        resolved = {
            "WORKFLOW_STEP": step_name,
            "STEP_OUTPUT": json.dumps(step.output.model_dump()),
        }
        monkeypatch.setattr(Engine, "_handoff_config", lambda self: resolved)
        monkeypatch.setattr(Engine, "_requirements_body", lambda self: _requirements())
        engine._check_coverage({"answer": _section("coverage", coverage)})

    def test_the_plan_step_is_refused(self, tmp_path, monkeypatch):
        with pytest.raises(CoverageValidationError):
            self._check(
                tmp_path,
                monkeypatch,
                "defaults/workflows/default.yaml",
                "plan",
                _renumbered_coverage(),
            )

    def test_a_critique_reporting_gaps_is_not_refused(self, tmp_path, monkeypatch):
        self._check(
            tmp_path,
            monkeypatch,
            ".nebuko/workflows/spec-tdd.yaml",
            "critique",
            "| R1 | plan step 2 | no red test: gap |",
        )


class TestPlanReceivesTheRequirements:
    @pytest.mark.parametrize("workflow", WORKFLOWS)
    def test_every_enforcing_workflow_wires_the_section(self, workflow):
        definition, plan = _step(workflow, "plan")

        assert plan.inputs["requirements"].ref == "analyze.requirements"
        assert plan.inputs["requirements"].required is False
        assert "requirements" in [
            field.name
            for step in definition.steps
            if step.name == "analyze"
            for field in step.output.fields
        ]

    @pytest.mark.parametrize("workflow", WORKFLOWS)
    def test_the_prompt_input_is_the_section_alone(self, workflow):
        definition, plan = _step(workflow, "plan")
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": build_step_inputs(definition, plan)}
        )

        results = materialize(
            step_inputs,
            {"analyze": "whole comment", "analyze.requirements": _requirements()},
        )

        assert step_inputs.required_failures(results) == []
        assert results[0].body == _requirements()
        assert "whole comment" not in render(results)

    def test_a_thread_without_analyze_still_runs_the_plan(self):
        definition, plan = _step("defaults/workflows/default.yaml", "plan")
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": build_step_inputs(definition, plan)}
        )

        results = materialize(step_inputs, {})

        assert step_inputs.required_failures(results) == []
        assert "unavailable" in render(results)

    def test_the_section_is_read_from_the_analyze_comment(self, tmp_path, monkeypatch):
        from nebuko.engine.lib.workflow_state import to_git_comment
        from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState
        from nebuko.engine.models import CommentEntry

        definition, _ = _step("defaults/workflows/default.yaml", "plan")
        declared = {s.name: s.output for s in definition.steps if s.output is not None}
        monkeypatch.setattr(Engine, "_declared_outputs", lambda self: declared)
        state = WorkflowState(workflow="default", step="analyze", agent="architect")
        body = "\n".join(
            ["# Analysis", _section("requirements", _requirements()), to_git_comment(state)]
        )

        scope = _engine(tmp_path)._step_output_scope(
            [CommentEntry(author="bot", body=body, created_at="t", comment_id="1")]
        )

        assert scope["analyze.requirements"].strip() == _requirements().strip()


class TestDottedInputsAddToTheDefault:
    def _spec(self, tmp_path, monkeypatch, path: list[str]):
        monkeypatch.setattr(
            Engine,
            "_workflow_path",
            lambda self: [WorkflowPathEntry(step=name) for name in path],
        )
        monkeypatch.setattr(
            Engine,
            "_workflow_dirs",
            lambda self: (str(ROOT / "defaults/workflows"), ""),
        )
        return _engine(tmp_path)._step_context_spec(
            {"WORKFLOW_NAME": "default", "WORKFLOW_STEP": "plan"}
        )

    def test_plan_keeps_the_previous_step(self, tmp_path, monkeypatch):
        spec = self._spec(tmp_path, monkeypatch, ["analyze", "context"])

        assert spec.inputs == (ContextInput(ContextKind.AGENT_OUTPUT, "context"),)

    def test_a_replanning_run_keeps_its_own_plan(self, tmp_path, monkeypatch):
        spec = self._spec(tmp_path, monkeypatch, ["analyze", "context", "plan"])

        assert spec.inputs == (
            ContextInput(ContextKind.AGENT_OUTPUT, "context"),
            ContextInput(ContextKind.AGENT_OUTPUT, "plan"),
        )

    def test_a_bare_step_input_still_replaces_the_default(self):
        from nebuko.engine.lib.config.sub_domains.workflow.input_binding import InputBinding
        from types import SimpleNamespace

        step = SimpleNamespace(
            inputs={
                "whole": InputBinding.model_validate("plan"),
                "body": InputBinding.model_validate("issue_body"),
                "section": InputBinding.model_validate("plan.headline"),
                "param": InputBinding.model_validate("input.setup"),
                "constant": InputBinding.model_validate({"str": "x"}),
            }
        )

        assert Engine._declared_context_inputs(step) == [
            ContextInput(ContextKind.AGENT_OUTPUT, "plan"),
            ContextInput(ContextKind.ISSUE_BODY),
        ]


class TestBindingPayload:
    def test_a_required_binding_keeps_its_payload_and_still_fails(self):
        definition = load_workflow(str(ROOT / "defaults/workflows/spec-driven-impl.yaml"))
        step = next(s for s in definition.steps if s.inputs)

        payload = json.loads(build_step_inputs(definition, step))
        assert all("required" not in binding for binding in payload["consumer"].values())

        step_inputs = StepInputs.from_resolved({"STEP_INPUTS": json.dumps(payload)})
        results = materialize(step_inputs, {})
        assert step_inputs.required_failures(results) == results
