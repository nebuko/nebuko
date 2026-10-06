"""Tests for the output validation utility (validate_output.py)."""

import json
import unittest

from validate_output import (
    ExtractResult,
    ValidationResult,
    extract_agent_run_configs,
    extract_dispatch,
    extract_dispatch_resilient,
    has_agent_run_config_marker,
    has_unterminated_agent_run_config,
    strip_agent_run_config,
    validate_dispatch,
    validate_agent_run_config,
    validate_forbidden_sections,
    validate_agent_output,
    VALID_DISPATCH_STATUSES,
)
from workflow_state.sub_domains.dispatch.dispatch import DISPATCH_STATUSES


# The exact #511 payload shape: sentinel line-broken across two lines and
# wrapped in stray markdown fences.
ISSUE_511_SHAPE = (
    'Done here.\n'
    "```\n"
    '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"},\n'
    '  "function_call": {"function": "call_agent", "agent_name": "ctx"}} -->\n'
    "```"
)


class TestExtractDispatchResilient:
    def test_line_broken_fence_wrapped_sentinel(self):
        result = extract_dispatch_resilient(ISSUE_511_SHAPE)
        assert result.parse_error is None
        data = json.loads(result.payload)
        assert data["dispatch"]["status"] == "advance"
        assert data["function_call"]["agent_name"] == "ctx"

    def test_single_line_multiline_json_sentinel(self):
        text = '<!-- AGENT_RUN_CONFIG {\n  "dispatch": {"status": "advance"}\n} -->'
        result = extract_dispatch_resilient(text)
        assert result.parse_error is None
        assert '"status": "advance"' in result.payload

    def test_no_sentinel_returns_none_without_error(self):
        result = extract_dispatch_resilient("Plain final message, no sentinel.")
        assert result.payload is None
        assert result.parse_error is None

    def test_truncated_sentinel_reports_diagnostic(self):
        result = extract_dispatch_resilient('Partial output <!-- AGENT_RUN_CONFIG {"dispatch": {"stat')
        assert result.payload is None
        assert result.parse_error == "truncated JSON object"

    def test_wellformed_single_line_unchanged(self):
        text = (
            'All good.\n'
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, '
            '"function_call": {"function": "call_agent", "agent_name": "dev"}} -->'
        )
        result = extract_dispatch_resilient(text)
        assert result.parse_error is None
        assert result.payload == (
            '{"dispatch": {"status": "advance"}, '
            '"function_call": {"function": "call_agent", "agent_name": "dev"}}'
        )

    def test_last_valid_wins_across_both_sources(self):
        text = (
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "hold"}} -->\n'
            "```\n"
            '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}}\n'
            "-->\n```\n"
        )
        result = extract_dispatch_resilient(text)
        assert result.parse_error is None
        assert result.payload == '{"dispatch": {"status": "advance"}}'

    def test_malformed_json_reports_reason(self):
        text = '<!-- AGENT_RUN_CONFIG {not valid json at all} -->'
        result = extract_dispatch_resilient(text)
        assert result.payload is None
        assert result.parse_error is not None
        assert "unparseable JSON" in result.parse_error

    def test_non_object_json_reports_reason(self):
        text = '<!-- AGENT_RUN_CONFIG [1, 2, 3] -->'
        result = extract_dispatch_resilient(text)
        assert result.payload is None
        assert result.parse_error == "JSON is not an object"

    def test_return_type_is_extract_result(self):
        assert isinstance(extract_dispatch_resilient(""), ExtractResult)


class TestHasAgentRunConfigMarker:
    def test_detects_marker(self):
        assert has_agent_run_config_marker(ISSUE_511_SHAPE) is True

    def test_detects_truncated_marker(self):
        assert has_agent_run_config_marker("<!-- AGENT_RUN_CONFIG {\"dispat") is True

    def test_whitespace_variant(self):
        assert has_agent_run_config_marker("<!--   AGENT_RUN_CONFIG   {} -->") is True

    def test_no_marker(self):
        assert has_agent_run_config_marker("Just a plain answer.") is False

    def test_empty_text(self):
        assert has_agent_run_config_marker("") is False


class TestStripLenientSentinels:
    def test_strips_line_broken_fence_wrapped_sentinel(self):
        stripped = strip_agent_run_config(ISSUE_511_SHAPE)
        assert "AGENT_RUN_CONFIG" not in stripped
        assert "call_agent" not in stripped
        assert "Done here." in stripped

    def test_strips_truncated_sentinel_preserves_prose(self):
        stripped = strip_agent_run_config('Partial output <!-- AGENT_RUN_CONFIG {"dispatch": {"stat')
        assert "AGENT_RUN_CONFIG" not in stripped
        assert "Partial output" in stripped

    def test_nop_when_no_sentinel(self):
        text = "Clean final message."
        assert strip_agent_run_config(text) == text

    def test_preserves_agent_metrics(self):
        text = (
            "Answer\n"
            "<!-- agent-metrics: {\"run_id\": \"abc\"} -->"
        )
        assert strip_agent_run_config(text) == text


class TestValidationResult:
    def test_initial_valid(self):
        result = ValidationResult(is_valid=True)
        assert result.is_valid is True
        assert result.errors == []

    def test_add_error_invalidates(self):
        result = ValidationResult(is_valid=True)
        result.add_error("test error")
        assert result.is_valid is False
        assert result.errors == ["test error"]


class TestExtractAgentRunConfigs:
    def test_extract_single_config(self):
        text = """Some text before
<!-- AGENT_RUN_CONFIG {"task": "dev", "complexity": "high"} -->
Some text after"""
        configs = extract_agent_run_configs(text)
        assert len(configs) == 1
        assert configs[0] == '{"task": "dev", "complexity": "high"}'

    def test_extract_multiple_configs(self):
        text = """
<!-- AGENT_RUN_CONFIG {"task": "genius"} -->
Middle text.
<!-- AGENT_RUN_CONFIG {"task": "dev", "complexity": "low"} -->
"""
        configs = extract_agent_run_configs(text)
        assert len(configs) == 2
        assert configs[0] == '{"task": "genius"}'
        assert configs[1] == '{"task": "dev", "complexity": "low"}'

    def test_extract_no_configs(self):
        text = "No AGENT_RUN_CONFIG sentinels here."
        configs = extract_agent_run_configs(text)
        assert len(configs) == 0

    def test_extract_empty_payload_ignored(self):
        text = "<!-- AGENT_RUN_CONFIG -->"
        configs = extract_agent_run_configs(text)
        assert len(configs) == 0

    def test_extract_whitespace_variants(self):
        text = "<!--   AGENT_RUN_CONFIG   {\"task\":\"dev\"}   -->"
        configs = extract_agent_run_configs(text)
        assert len(configs) == 1
        assert configs[0] == '{"task":"dev"}'

    def test_legacy_xml_not_parsed(self):
        """The legacy <agentRunConfig> XML block is no longer a parsing path."""
        text = '<agentRunConfig>\n{"task": "dev"}\n</agentRunConfig>'
        configs = extract_agent_run_configs(text)
        assert len(configs) == 0

    def test_multiline_sentinel_not_parsed(self):
        """A quoted multi-line example is deliberately ignored (single-line only)."""
        text = """```
<!-- AGENT_RUN_CONFIG
{"task": "dev"}
-->
```"""
        configs = extract_agent_run_configs(text)
        assert len(configs) == 0


class TestExtractDispatch:
    def test_last_valid_wins(self):
        text = (
            '<!-- AGENT_RUN_CONFIG {"task": "genius"} -->\n'
            "middle\n"
            '<!-- AGENT_RUN_CONFIG {"task": "dev", "complexity": "low"} -->\n'
        )
        assert extract_dispatch(text) == '{"task": "dev", "complexity": "low"}'

    def test_invalid_json_skipped(self):
        text = (
            '<!-- AGENT_RUN_CONFIG {"task": "genius"} -->\n'
            "<!-- AGENT_RUN_CONFIG {not valid json} -->\n"
        )
        assert extract_dispatch(text) == '{"task": "genius"}'

    def test_non_dict_json_skipped(self):
        text = (
            '<!-- AGENT_RUN_CONFIG [1, 2, 3] -->\n'
            '<!-- AGENT_RUN_CONFIG {"task": "ctx"} -->\n'
        )
        assert extract_dispatch(text) == '{"task": "ctx"}'

    def test_no_sentinel_returns_none(self):
        assert extract_dispatch("plain message, no sentinel") is None

    def test_truncated_sentinel_returns_none(self):
        text = 'done\n<!-- AGENT_RUN_CONFIG {"task": "dev", "comple'
        assert extract_dispatch(text) is None


class TestUnterminatedSentinel:
    def test_detects_truncation(self):
        text = 'done\n<!-- AGENT_RUN_CONFIG {"task": "dev", "comple'
        assert has_unterminated_agent_run_config(text) is True

    def test_complete_sentinel_not_truncated(self):
        text = 'done\n<!-- AGENT_RUN_CONFIG {"task": "dev"} -->'
        assert has_unterminated_agent_run_config(text) is False

    def test_complete_then_truncated(self):
        text = (
            '<!-- AGENT_RUN_CONFIG {"task": "genius"} -->\n'
            'tail\n<!-- AGENT_RUN_CONFIG {"task": "dev"'
        )
        assert has_unterminated_agent_run_config(text) is True

    def test_whitespace_variant_detected(self):
        text = 'done\n<!--   AGENT_RUN_CONFIG {"task"'
        assert has_unterminated_agent_run_config(text) is True

    def test_no_marker(self):
        assert has_unterminated_agent_run_config("just prose") is False


class TestStripAgentRunConfig:
    def test_removes_single_sentinel(self):
        msg = 'Hello world\n<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->'
        assert strip_agent_run_config(msg) == "Hello world"

    def test_removes_multiple_sentinels(self):
        msg = (
            "First part\n"
            '<!-- AGENT_RUN_CONFIG {"task": "genius"} -->\n'
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

    def test_preserves_agent_metrics(self):
        msg = (
            "Content\n"
            "<!-- agent-metrics: {} -->\n"
            '<!-- AGENT_RUN_CONFIG {"status": "advance"} -->'
        )
        result = strip_agent_run_config(msg)
        assert "agent-metrics" in result
        assert "AGENT_RUN_CONFIG" not in result


class TestValidateAgentRunConfig:
    def test_valid_minimal(self):
        result = validate_agent_run_config('{"task": "dev"}')
        assert result.is_valid is True

    def test_valid_full(self):
        result = validate_agent_run_config(
            '{"task": "dev", "complexity": "high", "reasoning": "high", '
            '"temperature": 0.3, "top_p": 0.95, "top_k": 50, '
            '"min_p": 0.0, "presence_penalty": 1.5, '
            '"repetition_penalty": 1.0, "round": 2}'
        )
        assert result.is_valid is True

    def test_po_task_valid(self):
        result = validate_agent_run_config('{"task": "po"}')
        assert result.is_valid is True

    def test_debug_task_valid(self):
        result = validate_agent_run_config('{"task": "debug"}')
        assert result.is_valid is True

    def test_missing_task(self):
        result = validate_agent_run_config('{"complexity": "high"}')
        assert result.is_valid is False
        assert any("missing required field" in e for e in result.errors)

    def test_dispatch_only_valid(self):
        """Defect D1 regression (issue #765): the dispatch-only sentinel ctx
        is told to emit must validate."""
        result = validate_agent_run_config('{"dispatch": {"status": "advance"}}')
        assert result.is_valid is True

    def test_function_call_only_valid(self):
        result = validate_agent_run_config(
            '{"function_call": {"function": "call_agent", "agent_name": "ctx"}}'
        )
        assert result.is_valid is True

    def test_task_with_function_call_rejected(self):
        result = validate_agent_run_config(
            '{"task": "genius", '
            '"function_call": {"function": "call_agent", "agent_name": "ctx"}}'
        )
        assert result.is_valid is False
        assert any("cannot combine" in e for e in result.errors)

    def test_invalid_task(self):
        result = validate_agent_run_config('{"task": "invalid_role"}')
        assert result.is_valid is False
        assert any("Invalid task" in e for e in result.errors)

    def test_invalid_complexity(self):
        result = validate_agent_run_config('{"task": "dev", "complexity": "extreme"}')
        assert result.is_valid is False
        assert any("Invalid complexity" in e for e in result.errors)

    def test_invalid_reasoning(self):
        result = validate_agent_run_config('{"task": "dev", "reasoning": "ultra"}')
        assert result.is_valid is False
        assert any("Invalid reasoning" in e for e in result.errors)

    def test_invalid_json(self):
        result = validate_agent_run_config("{invalid json}")
        assert result.is_valid is False

    def test_temperature_wrong_type(self):
        result = validate_agent_run_config('{"task": "dev", "temperature": "high"}')
        assert result.is_valid is False
        assert any("temperature" in e and "must be a number" in e for e in result.errors)

    def test_top_p_wrong_type(self):
        result = validate_agent_run_config('{"task": "dev", "top_p": "0.95"}')
        assert result.is_valid is False
        assert any("top_p" in e and "must be a number" in e for e in result.errors)

    def test_top_k_wrong_type(self):
        result = validate_agent_run_config('{"task": "dev", "top_k": "50"}')
        assert result.is_valid is False
        assert any("top_k" in e and "must be a number" in e for e in result.errors)

    def test_repetition_penalty_wrong_type(self):
        result = validate_agent_run_config('{"task": "dev", "repetition_penalty": "1.1"}')
        assert result.is_valid is False
        assert any("repetition_penalty" in e and "must be a number" in e for e in result.errors)

    def test_top_p_out_of_range_high(self):
        result = validate_agent_run_config('{"task": "dev", "top_p": 1.5}')
        assert result.is_valid is False
        assert any("top_p" in e and "range" in e.lower() for e in result.errors)

    def test_top_p_out_of_range_low(self):
        result = validate_agent_run_config('{"task": "dev", "top_p": -0.1}')
        assert result.is_valid is False
        assert any("top_p" in e and "range" in e.lower() for e in result.errors)

    def test_top_k_out_of_range(self):
        result = validate_agent_run_config('{"task": "dev", "top_k": -1}')
        assert result.is_valid is False
        assert any("top_k" in e and "positive" in e.lower() for e in result.errors)

    def test_repetition_penalty_out_of_range(self):
        result = validate_agent_run_config('{"task": "dev", "repetition_penalty": -0.5}')
        assert result.is_valid is False
        assert any("repetition_penalty" in e and "positive" in e.lower() for e in result.errors)

    def test_min_p_wrong_type(self):
        result = validate_agent_run_config('{"task": "dev", "min_p": "0.0"}')
        assert result.is_valid is False
        assert any("min_p" in e and "must be a number" in e for e in result.errors)

    def test_min_p_out_of_range_high(self):
        result = validate_agent_run_config('{"task": "dev", "min_p": 1.5}')
        assert result.is_valid is False
        assert any("min_p" in e and "range" in e.lower() for e in result.errors)

    def test_min_p_out_of_range_low(self):
        result = validate_agent_run_config('{"task": "dev", "min_p": -0.1}')
        assert result.is_valid is False
        assert any("min_p" in e and "range" in e.lower() for e in result.errors)

    def test_presence_penalty_wrong_type(self):
        result = validate_agent_run_config('{"task": "dev", "presence_penalty": "1.5"}')
        assert result.is_valid is False
        assert any("presence_penalty" in e and "must be a number" in e for e in result.errors)

    def test_presence_penalty_out_of_range_high(self):
        result = validate_agent_run_config('{"task": "dev", "presence_penalty": 3.0}')
        assert result.is_valid is False
        assert any("presence_penalty" in e and "range" in e.lower() for e in result.errors)

    def test_presence_penalty_out_of_range_low(self):
        result = validate_agent_run_config('{"task": "dev", "presence_penalty": -3.0}')
        assert result.is_valid is False
        assert any("presence_penalty" in e and "range" in e.lower() for e in result.errors)

    def test_round_wrong_type(self):
        result = validate_agent_run_config('{"task": "dev", "round": "two"}')
        assert result.is_valid is False
        assert any("round" in e and "must be an integer" in e for e in result.errors)

    def test_json_array_rejected(self):
        result = validate_agent_run_config('[{"task": "dev"}]')
        assert result.is_valid is False


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

    def test_no_false_positive_in_body_text(self):
        """Words like 'resolution' containing 'solution' should not trigger."""
        result = validate_forbidden_sections(
            "### RESOLUTION\nThis issue has been resolved successfully."
        )
        assert result.is_valid is True

    def test_custom_forbidden_list(self):
        result = validate_forbidden_sections("## SECRET\n...", forbidden_headers=["SECRET"])
        assert result.is_valid is False

    def test_multiple_forbidden(self):
        text = "## ROOT CAUSE\n...\n## SOLUTION\n..."
        result = validate_forbidden_sections(text)
        assert result.is_valid is False
        assert len(result.errors) == 2


class TestValidateAgentOutput:
    def test_dev_valid_output(self):
        text = """I've implemented the changes.

<!-- AGENT_RUN_CONFIG {"task": "review", "complexity": "medium"} -->
"""
        result = validate_agent_output(text, "dev")
        assert result.is_valid is True

    def test_ctx_output_with_forbidden_sections(self):
        text = """Here are the files.

## ROOT CAUSE
The issue is a null pointer.

## SOLUTION
Add a null check.
"""
        result = validate_agent_output(text, "ctx")
        assert result.is_valid is False
        assert any("ROOT CAUSE" in e for e in result.errors)
        assert any("SOLUTION" in e for e in result.errors)

    def test_ctx_output_valid(self):
        text = """### FILES FOUND
1. `config.py` (lines 79-91) — Pydantic model for LLM config.
2. `agent_script.py` (lines 454-561) — LLM configuration resolution.

### RELEVANT SNIPPETS
**`config.py` — LLMConfig (lines 79-91):**
```python
class LLMConfig(BaseModel):
    model_path: str
```
"""
        result = validate_agent_output(text, "ctx")
        assert result.is_valid is True

    def test_invalid_config_in_output(self):
        text = """Done.
<!-- AGENT_RUN_CONFIG {"task": "invalid_role"} -->
"""
        result = validate_agent_output(text, "genius")
        assert result.is_valid is False

    def test_empty_text(self):
        result = validate_agent_output("", "dev")
        assert result.is_valid is True


class TestValidateDispatchStatuses(unittest.TestCase):
    """The validator must accept every status the Dispatch model accepts (#704)."""

    def test_accepts_soft_kill_error_status(self):
        result = validate_dispatch({"status": "soft_kill"})
        self.assertTrue(result.is_valid)

    def test_rejects_unknown_status(self):
        result = validate_dispatch({"status": "definitely_not_a_status"})
        self.assertFalse(result.is_valid)
        self.assertTrue(any("definitely_not_a_status" in e for e in result.errors))

    def test_valid_statuses_track_canonical_set(self):
        # Guards the single-source-of-truth wiring: no duplicated literal may
        # drift from workflow_state.DISPATCH_STATUSES.
        self.assertEqual(VALID_DISPATCH_STATUSES, set(DISPATCH_STATUSES))
