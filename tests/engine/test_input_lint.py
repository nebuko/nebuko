"""Wiring-lint scenarios for typed workflow inputs (issue #813, rules L8–L10).

The workflow linter type-checks a workflow's call sites like a compiler checks
a function call: every declared input bound, kinds matching (a ``ref`` being
kind-preserving), and every reference resolvable in scope.
"""

from __future__ import annotations

from pathlib import Path

from nebuko.engine.lint import WorkflowLinter

FIXTURES = Path(__file__).parent / "input_lint_fixtures"


def _lint(fixture: str, *, strict: bool = False):
    return WorkflowLinter(strict=strict).lint(FIXTURES / fixture)


def _tuples(issues) -> set[tuple[str, str, str | None]]:
    return {(issue.rule, issue.severity, issue.step) for issue in issues}


def _wiring(issues):
    return {t for t in _tuples(issues) if t[0].startswith(("L8", "L9", "L10"))}


class TestCleanWiring:
    def test_fully_bound_call_site_reports_no_wiring_issue(self):
        assert _wiring(_lint("clean_wired.yaml")) == set()


class TestCallSiteRules:
    def test_unbound_required_input_is_an_error(self):
        assert _wiring(_lint("l8_unbound_required.yaml")) == {
            ("L8-input-binding", "error", "call_callee")
        }

    def test_binding_an_undeclared_name_is_an_error(self):
        issues = _lint("l8_undeclared_binding.yaml")
        assert ("L8-input-binding", "error", "call_callee") in _wiring(issues)
        assert any("ghost" in issue.message for issue in issues)

    def test_literal_kind_mismatch_is_an_error(self):
        issues = _lint("l9_kind_mismatch.yaml")
        assert ("L9-input-kind", "error", "call_callee") in _wiring(issues)

    def test_non_kind_preserving_ref_is_an_error(self):
        issues = _lint("l9_ref_kind_mismatch.yaml")
        assert ("L9-input-kind", "error", "call_callee") in _wiring(issues)


class TestConsumerRules:
    def test_unresolvable_ref_is_a_warning(self):
        assert _wiring(_lint("l10_unresolved_ref.yaml")) == {
            ("L10-input-ref", "warning", "work")
        }

    def test_warning_is_promoted_to_error_in_strict_mode(self):
        assert ("L10-input-ref", "error", "work") in _wiring(
            _lint("l10_unresolved_ref.yaml", strict=True)
        )

    def test_declared_but_unused_input_is_reported(self):
        """A declared input that no step consumes or forwards is flagged."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unused.yaml"
            path.write_text(
                "name: unused\n"
                "description: d\n"
                "trigger:\n  agents: [dev]\n"
                "inputs:\n  - name: diff\n    kind: exec\n"
                "steps:\n  - name: work\n    agent: dev\n    next: null\n",
                encoding="utf-8",
            )
            issues = WorkflowLinter().lint(path)
        assert any(
            issue.rule == "L10-input-ref" and "never consumed" in issue.message
            for issue in issues
        )
