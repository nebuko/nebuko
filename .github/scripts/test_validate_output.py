"""Tests for the output validation utility (validate_output.py)."""

from validate_output import (
    ValidationResult,
    extract_agent_run_configs,
    should_retry_agent,
    validate_agent_run_config,
    validate_forbidden_sections,
    validate_agent_output,
    MAX_RETRIES,
)


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
<agentRunConfig>
{"task": "dev", "complexity": "high"}
</agentRunConfig>
Some text after"""
        configs = extract_agent_run_configs(text)
        assert len(configs) == 1
        assert configs[0] == '{"task": "dev", "complexity": "high"}'

    def test_extract_multiple_configs(self):
        text = """
<agentRunConfig>
{"task": "genius"}
</agentRunConfig>
Middle text.
<agentRunConfig>
{"task": "dev", "complexity": "low"}
</agentRunConfig>
"""
        configs = extract_agent_run_configs(text)
        assert len(configs) == 2
        assert configs[0] == '{"task": "genius"}'
        assert configs[1] == '{"task": "dev", "complexity": "low"}'

    def test_extract_no_configs(self):
        text = "No agentRunConfig blocks here."
        configs = extract_agent_run_configs(text)
        assert len(configs) == 0

    def test_extract_empty_block_ignored(self):
        text = "<agentRunConfig>\n\n</agentRunConfig>"
        configs = extract_agent_run_configs(text)
        assert len(configs) == 0

    def test_extract_compact_format(self):
        text = "<agentRunConfig>{\"task\":\"review\",\"complexity\":\"medium\"}</agentRunConfig>"
        configs = extract_agent_run_configs(text)
        assert len(configs) == 1


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

    def test_missing_task(self):
        result = validate_agent_run_config('{"complexity": "high"}')
        assert result.is_valid is False
        assert any("missing required field" in e for e in result.errors)

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

<agentRunConfig>
{"task": "review", "complexity": "medium"}
</agentRunConfig>
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
<agentRunConfig>
{"task": "invalid_role"}
</agentRunConfig>
"""
        result = validate_agent_output(text, "genius")
        assert result.is_valid is False

    def test_empty_text(self):
        result = validate_agent_output("", "dev")
        assert result.is_valid is True


class TestShouldRetryAgent:
    def test_missing_handoff_requires_retry(self):
        text = "Here is my analysis. No hand-off block."
        result = should_retry_agent(text, "dev", retry_count=0)
        assert result["RETRY"] == "true"
        assert result["AGENT"] == "dev"
        assert result["ESCALATE"] == "false"

    def test_valid_handoff_no_retry(self):
        text = """Done with work.
<agentRunConfig>
{"task": "review", "complexity": "medium"}
</agentRunConfig>
"""
        result = should_retry_agent(text, "dev", retry_count=0)
        assert result["RETRY"] == "false"

    def test_wait_for_human_no_retry(self):
        text = """Proposal complete. Awaiting human approval.
<agentRunConfig>
{"task": "wait_for_human"}
</agentRunConfig>
"""
        result = should_retry_agent(text, "genius", retry_count=0)
        assert result["RETRY"] == "false"

    def test_max_retries_escalate_to_genius(self):
        text = "Incomplete output without agentRunConfig."
        result = should_retry_agent(text, "dev", retry_count=MAX_RETRIES)
        assert result["RETRY"] == "true"
        assert result["AGENT"] == "genius"
        assert result["ESCALATE"] == "true"

    def test_below_max_retries_no_escalation(self):
        text = "Incomplete output."
        result = should_retry_agent(text, "ctx", retry_count=1)
        assert result["RETRY"] == "true"
        assert result["AGENT"] == "ctx"
        assert result["ESCALATE"] == "false"

    def test_empty_comment_requires_retry(self):
        result = should_retry_agent("", "dev", retry_count=0)
        assert result["RETRY"] == "true"

    def test_malformed_json_still_detected(self):
        text = """Work done.
<agentRunConfig>
{bad json}
</agentRunConfig>
"""
        # Malformed JSON in agentRunConfig — treat as missing hand-off → retry
        result = should_retry_agent(text, "dev", retry_count=0)
        assert result["RETRY"] == "true"
        assert result["AGENT"] == "dev"

    def test_wait_for_human_any_role(self):
        text = """Approved!
<agentRunConfig>
{"task": "wait_for_human"}
</agentRunConfig>
"""
        result = should_retry_agent(text, "review", retry_count=0)
        assert result["RETRY"] == "false"

    def test_escalation_at_exactly_max_retries(self):
        text = "No handoff."
        result = should_retry_agent(text, "ctx", retry_count=MAX_RETRIES)
        assert result["AGENT"] == "genius"

    def test_no_escalation_just_below_max(self):
        text = "No handoff."
        result = should_retry_agent(text, "ctx", retry_count=MAX_RETRIES - 1)
        assert result["AGENT"] == "ctx"
        assert result["ESCALATE"] == "false"
