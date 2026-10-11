"""Tests for step-output resolution (issue #756, ADR-0013).

A step's output *is* its posted comment, addressed by the step name. These
tests pin the join of the persisted locator (step -> comment id) with the
comment index (id -> state-stripped body), the degradation reasons, and the
single-pass, non-recursive ``{{ <step> }}`` prompt substitution.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from workflow_state.step_output_record import StepOutputRecord
from workflow_state.features.resolve_step_output.resolve_step_output import (
    PROMPT_REF_PATTERN,
    ResolvedOutput,
    render_prompt_refs,
    resolve_step_output,
)


class TestResolveStepOutput(unittest.TestCase):
    def setUp(self):
        self.records = {
            "plan": StepOutputRecord(step="plan", comment_id="11"),
            "implement": StepOutputRecord(step="implement", comment_id=None),
        }
        self.comment_index = {"11": "the plan body"}

    def test_resolves_to_the_comment_body(self):
        result = resolve_step_output(self.records, self.comment_index, "plan")
        self.assertTrue(result.ok)
        self.assertEqual(result.body, "the plan body")
        self.assertIsNone(result.detail)

    def test_unknown_step_is_not_recorded(self):
        result = resolve_step_output(self.records, self.comment_index, "ghost")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "not recorded")

    def test_missing_comment_id_reports_no_comment_recorded(self):
        result = resolve_step_output(self.records, self.comment_index, "implement")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "no comment recorded")

    def test_comment_absent_from_index_reports_not_found(self):
        records = {"plan": StepOutputRecord(step="plan", comment_id="99")}
        result = resolve_step_output(records, self.comment_index, "plan")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "comment not found")

    def test_comment_id_int_is_coerced_to_string(self):
        record = StepOutputRecord(step="plan", comment_id=11)
        self.assertEqual(record.comment_id, "11")
        result = resolve_step_output({"plan": record}, {"11": "b"}, "plan")
        self.assertTrue(result.ok)


class TestRenderPromptRefs(unittest.TestCase):
    def _resolved(self, **bodies):
        return {
            step: ResolvedOutput(step=step, ok=True, body=body)
            for step, body in bodies.items()
        }

    def test_substitutes_a_declared_reference(self):
        prompt = "Plan: {{ plan }} end."
        rendered = render_prompt_refs(prompt, self._resolved(plan="THE PLAN"))
        self.assertEqual(rendered, "Plan: THE PLAN end.")

    def test_whitespace_inside_braces_is_tolerated(self):
        rendered = render_prompt_refs("{{plan }}", self._resolved(plan="X"))
        self.assertEqual(rendered, "X")

    def test_unresolved_reference_degrades(self):
        rendered = render_prompt_refs("{{ ghost }}", {})
        self.assertEqual(rendered, "_(unavailable: ghost)_")

    def test_failed_result_degrades(self):
        resolved = {"plan": ResolvedOutput(step="plan", ok=False, detail="x")}
        rendered = render_prompt_refs("{{ plan }}", resolved)
        self.assertEqual(rendered, "_(unavailable: plan)_")

    def test_substituted_body_is_never_re_scanned(self):
        """A body containing ``{{ }}`` cannot induce recursion (single pass)."""
        body = "literal {{ implement }} stays"
        rendered = render_prompt_refs("{{ plan }}", self._resolved(plan=body))
        self.assertEqual(rendered, body)

    def test_empty_prompt_is_returned_unchanged(self):
        self.assertEqual(render_prompt_refs("", {}), "")

    def test_pattern_only_matches_step_names(self):
        # ``{{ 1bad }}`` and ``{{ a.b }}`` are not step-name references.
        self.assertEqual(PROMPT_REF_PATTERN.findall("{{ 1bad }} {{ a.b }}"), [])


if __name__ == "__main__":
    unittest.main()
