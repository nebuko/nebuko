"""Tests for the output validation utility (validate_output.py)."""

from validate_output import (
    ValidationResult,
    extract_agent_run_configs,
    validate_agent_run_config,
    validate_forbidden_sections,
    validate_agent_output,
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
            '"repetition_penalty": 1.1, "round": 2}'
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
