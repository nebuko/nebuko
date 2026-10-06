"""Spec-scenario tests for the workflow graph linter (issue #751).

The Pydantic loader (``engine/lib/config/features/load_workflow``) validates
each workflow's field-level shape but cannot see graph-level defects — dangling
``next`` targets, unreachable steps, dead condition keys, unresumable human
gates, graphs with no terminal step. These scenarios pin the ``WorkflowLinter``
contract (rules L1–L7) ahead of implementation; they stay RED against the stub
because every member raises ``NotImplementedError``.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from nebuko.engine.lint import LintIssue, WorkflowLinter

FIXTURES = Path(__file__).parent / "lint_fixtures"

# Rules and severities fixed by the approved plan (Phase B, step 2): L2 is a
# warning that ``strict`` promotes; every other rule is an error.
RULE_SEVERITY = {
    "L1-dangling-next": "error",
    "L2-unreachable-step": "warning",
    "L3-duplicate-step-id": "error",
    "L4-condition-key": "error",
    "L5-terminal-consistency": "error",
    "L6-human-gate": "error",
    "L7-no-terminal": "error",
}


def _issue(**overrides) -> LintIssue:
    values = {
        "rule": "L1-dangling-next",
        "severity": "error",
        "message": "boom",
        "step": "analyze",
        "location": None,
        "hint": None,
    }
    values.update(overrides)
    return LintIssue(**values)


def _lint(fixture: str, *, strict: bool = False) -> list[LintIssue]:
    return WorkflowLinter(strict=strict).lint(FIXTURES / fixture)


def _tuples(issues: list[LintIssue]) -> set[tuple[str, str, str | None]]:
    return {(issue.rule, issue.severity, issue.step) for issue in issues}


class TestLintIssueContract:
    def test_field_order_matches_the_frozen_contract(self):
        assert [f.name for f in dataclasses.fields(LintIssue)] == [
            "rule",
            "severity",
            "message",
            "step",
            "location",
            "hint",
        ]

    def test_is_frozen(self):
        issue = _issue()
        with pytest.raises(dataclasses.FrozenInstanceError):
            issue.severity = "warning"

    def test_carries_the_rule_identity(self):
        issue = _issue()
        assert (issue.rule, issue.severity, issue.message) == (
            "L1-dangling-next",
            "error",
            "boom",
        )

    def test_carries_the_optional_context_fields(self):
        issue = _issue(step=None, location="workflows/x.yaml:12", hint="add the step")
        assert (issue.step, issue.location, issue.hint) == (
            None,
            "workflows/x.yaml:12",
            "add the step",
        )


class TestWorkflowLinterContract:
    def test_exposes_the_plan_rule_methods(self):
        for name in (
            "_check_dangling_next",
            "_check_reachability",
            "_check_duplicate_ids",
            "_check_condition_keys",
            "_check_terminal_consistency",
            "_check_human_gates",
            "_check_has_terminal",
        ):
            assert callable(getattr(WorkflowLinter, name))

    def test_defaults_to_non_strict(self):
        parameter = inspect.signature(WorkflowLinter.__init__).parameters["strict"]
        assert parameter.default is False


class TestRuleScenarios:
    def test_l1_flags_a_dangling_next_target(self):
        issues = _lint("l1_dangling_next.yaml")
        assert ("L1-dangling-next", "error", "analyze") in _tuples(issues)

    def test_l2_warns_on_an_unreachable_step(self):
        issues = _lint("l2_unreachable.yaml")
        assert ("L2-unreachable-step", "warning", "orphan") in _tuples(issues)

    def test_l3_flags_a_duplicate_step_id(self):
        issues = _lint("l3_duplicate_ids.yaml")
        assert ("L3-duplicate-step-id", "error", "dup") in _tuples(issues)

    def test_l4_flags_a_dead_condition_key(self):
        issues = _lint("l4_condition_keys.yaml")
        assert ("L4-condition-key", "error", "gate") in _tuples(issues)

    def test_l5_flags_inconsistent_terminal_routing(self):
        # A step that is non-terminal (its conditions route onward) yet declares
        # no linear ``next`` dead-ends on a branch-less dispatch.
        issues = _lint("l5_terminal_consistency.yaml")
        assert ("L5-terminal-consistency", "error", "analyze") in _tuples(issues)

    def test_l6_flags_an_unresumable_human_gate(self):
        issues = _lint("l6_human_gate.yaml")
        assert ("L6-human-gate", "error", "gate") in _tuples(issues)

    def test_l7_flags_a_graph_with_no_terminal_step(self):
        issues = _lint("l7_no_terminal.yaml")
        assert any(issue.rule == "L7-no-terminal" for issue in issues)

    def test_a_clean_workflow_reports_no_issues(self):
        assert _lint("clean.yaml") == []

    def test_reports_every_violation_not_just_the_first(self):
        rules = {issue.rule for issue in _lint("multi_error.yaml")}
        assert {"L1-dangling-next", "L3-duplicate-step-id"} <= rules

    def test_lint_all_concatenates_per_file_issues(self):
        issues = WorkflowLinter().lint_all(
            [FIXTURES / "clean.yaml", FIXTURES / "l1_dangling_next.yaml"]
        )
        assert any(issue.rule == "L1-dangling-next" for issue in issues)

    def test_strict_promotes_warnings_to_errors(self):
        issues = _lint("l2_unreachable.yaml", strict=True)
        assert ("L2-unreachable-step", "error", "orphan") in _tuples(issues)

    def test_every_reported_severity_matches_the_plan(self):
        for fixture in (
            "l1_dangling_next.yaml",
            "l2_unreachable.yaml",
            "l3_duplicate_ids.yaml",
            "l4_condition_keys.yaml",
            "l5_terminal_consistency.yaml",
            "l6_human_gate.yaml",
            "l7_no_terminal.yaml",
            "multi_error.yaml",
        ):
            for issue in _lint(fixture):
                assert issue.severity == RULE_SEVERITY[issue.rule]
