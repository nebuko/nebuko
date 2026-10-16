"""Tests for the output validation utility (validate_output.py).

The legacy ``AGENT_RUN_CONFIG`` read path (sentinel extraction, dispatch
extraction, dispatch/config validation) is gone (issue #964); this suite keeps
the live surface: the stale-echo strip, the forbidden-section check, the
role-level output check and the complexity-review contract.
"""

import unittest

from validate_output import (
    ValidationResult,
    strip_agent_run_config,
    validate_forbidden_sections,
    validate_agent_output,
    validate_complexity_review,
)


class TestStripAgentRunConfig:
    def test_removes_single_sentinel(self):
        msg = 'Hello world\n<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        assert strip_agent_run_config(msg) == "Hello world"

    def test_removes_multiple_sentinels(self):
        msg = (
            "First part\n"
            '<!-- AGENT_RUN_CONFIG {"task": "architect"} -->\n'
            "Middle part\n"
            '<!-- AGENT_RUN_CONFIG {"task": "dev"} -->\n'
            "Last part"
        )
        result = strip_agent_run_config(msg)
        assert "AGENT_RUN_CONFIG" not in result
        assert "First part" in result and "Middle part" in result and "Last part" in result
        assert "\n\n\n" not in result

    def test_removes_inline_sentinel(self):
        msg = "See config <!-- AGENT_RUN_CONFIG {\"task\": \"dev\"} --> here."
        assert strip_agent_run_config(msg) == "See config  here."

    def test_noop_when_no_sentinel(self):
        msg = "Just regular text without any sentinels"
        assert strip_agent_run_config(msg) == msg

    def test_truncated_sentinel_stripped_message_preserved(self):
        msg = 'Analysis complete with details.\n<!-- AGENT_RUN_CONFIG {"task": "dev", "comple'
        result = strip_agent_run_config(msg)
        assert result == "Analysis complete with details."
        assert "AGENT_RUN_CONFIG" not in result

    def test_preserves_non_sentinel_html_comments(self):
        msg = (
            "Content\n"
            "<!-- bot-workflow-terminal -->\n"
            '<!-- AGENT_RUN_CONFIG {"status": "advance"} -->'
        )
        result = strip_agent_run_config(msg)
        assert "bot-workflow-terminal" in result
        assert "AGENT_RUN_CONFIG" not in result


class TestValidationResult:
    def test_starts_valid(self):
        result = ValidationResult(is_valid=True)
        assert result.is_valid is True
        assert result.errors == []

    def test_add_error_invalidates(self):
        result = ValidationResult(is_valid=True)
        result.add_error("boom")
        assert result.is_valid is False
        assert result.errors == ["boom"]


class TestValidateForbiddenSections:
    def test_clean_text(self):
        result = validate_forbidden_sections("Here are the files I found.")
        assert result.is_valid is True

    def test_contains_root_cause(self):
        result = validate_forbidden_sections("## ROOT CAUSE\nThe issue is...")
        assert result.is_valid is False
        assert any("ROOT CAUSE" in e for e in result.errors)

    def test_contains_fix(self):
        result = validate_forbidden_sections("## PROPOSED CHANGES\n1. Fix...")
        assert result.is_valid is False
        assert any("PROPOSED CHANGES" in e for e in result.errors)

    def test_case_insensitive(self):
        result = validate_forbidden_sections("## root cause\n...")
        assert result.is_valid is False
        assert len(result.errors) == 1


class TestValidateAgentOutput:
    def test_dev_output_with_no_forbidden_sections(self):
        text = "I've implemented the changes.\n\nSee the diff for details."
        result = validate_agent_output(text, "dev")
        assert result.is_valid is True

    def test_context_output_with_forbidden_sections(self):
        text = """Here are the files.

## ROOT CAUSE
The issue is a null pointer.

## SOLUTION
Add a null check.
"""
        result = validate_agent_output(text, "context")
        assert result.is_valid is False
        assert any("ROOT CAUSE" in e for e in result.errors)
        assert any("SOLUTION" in e for e in result.errors)

    def test_context_output_valid(self):
        text = """### FILES FOUND
1. `config.py` (lines 79-91) - Pydantic model for LLM config.
2. `agent_script.py` (lines 454-561) - LLM configuration resolution.

### RELEVANT SNIPPETS
**`config.py` - LLMConfig (lines 79-91):**
```python
class LLMConfig(BaseModel):
    model_path: str
```
"""
        result = validate_agent_output(text, "context")
        assert result.is_valid is True

    def test_empty_text(self):
        result = validate_agent_output("", "dev")
        assert result.is_valid is True


class TestValidateComplexityReview(unittest.TestCase):
    """The complexity_review contract (issue #869): evidence + non-blank feeling."""

    @staticmethod
    def _bundle():
        from complexity_governor import EvidenceBundle

        return EvidenceBundle(llm={"loops": 2, "stalled_steps": 1})

    def test_valid_request_passes(self):
        result = validate_complexity_review(
            {"evidence": self._bundle(), "feeling": "loop detected"}
        )
        self.assertTrue(result.is_valid)

    def test_missing_evidence_rejected(self):
        result = validate_complexity_review({"feeling": "loop"})
        self.assertFalse(result.is_valid)
        self.assertTrue(any("evidence" in e for e in result.errors))

    def test_empty_evidence_rejected(self):
        from complexity_governor import EvidenceBundle

        result = validate_complexity_review(
            {"evidence": EvidenceBundle(), "feeling": "loop"}
        )
        self.assertFalse(result.is_valid)
        self.assertTrue(any("at least one family" in e for e in result.errors))

    def test_blank_feeling_rejected(self):
        result = validate_complexity_review(
            {"evidence": self._bundle(), "feeling": "   "}
        )
        self.assertFalse(result.is_valid)
        self.assertTrue(any("feeling" in e for e in result.errors))

    def test_accepts_model_instance(self):
        from complexity_governor import ComplexityReviewRequest

        request = ComplexityReviewRequest(
            evidence=self._bundle(), feeling="weird design", suggested_tier="high"
        )
        self.assertTrue(validate_complexity_review(request).is_valid)

    def test_rejects_invalid_evidence_dict(self):
        result = validate_complexity_review(
            {"evidence": {"human": {"changes_requested": "not-an-int"}}, "feeling": "x"}
        )
        self.assertFalse(result.is_valid)
