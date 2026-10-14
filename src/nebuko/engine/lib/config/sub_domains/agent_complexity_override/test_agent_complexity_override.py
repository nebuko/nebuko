"""Tests for AgentComplexityOverride."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import ProviderConfig, LLMConfig, AgentComplexityOverride


class TestAgentComplexityOverride:
    """Test AgentComplexityOverride Pydantic model."""

    def _llm(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        return LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)

    def test_model_alias_resolved(self):
        """``model`` alias populates ``model_ref``."""
        override = AgentComplexityOverride(model=self._llm())
        assert override.model_ref is not None
        assert override.model_ref.model_path == "openrouter/deepseek/v4"

    def test_all_fields_optional(self):
        """An empty override is valid — every field defaults to None."""
        override = AgentComplexityOverride()
        assert override.model_ref is None
        assert override.temperature is None
        assert override.reasoning is None
        assert override.top_p is None
        assert override.top_k is None
        assert override.min_p is None
        assert override.presence_penalty is None
        assert override.repetition_penalty is None

    def test_full_override(self):
        override = AgentComplexityOverride(
            model=self._llm(),
            temperature=0.9,
            reasoning="xhigh",
            top_p=0.8,
            top_k=40,
            min_p=0.05,
            presence_penalty=0.1,
            repetition_penalty=1.1,
        )
        assert override.temperature == 0.9
        assert override.reasoning == "xhigh"
        assert override.top_p == 0.8
        assert override.top_k == 40
        assert override.min_p == 0.05
        assert override.presence_penalty == 0.1
        assert override.repetition_penalty == 1.1

    def test_thinking_fields_default_none(self):
        """The per-tier thinking switch/effort default to None (inherit, #941)."""
        override = AgentComplexityOverride()
        assert override.thinking_enabled is None
        assert override.reasoning_effort is None

    def test_thinking_fields_parse(self):
        """Tier-level thinking_enabled/reasoning_effort are recognized (#941)."""
        override = AgentComplexityOverride(
            thinking_enabled=True, reasoning_effort="medium"
        )
        assert override.thinking_enabled is True
        assert override.reasoning_effort == "medium"

    def test_unknown_key_is_rejected(self):
        """A misplaced key fails at load instead of being silently dropped (#941)."""
        with pytest.raises(ValidationError):
            AgentComplexityOverride(enable_thinking=True)
