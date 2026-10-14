"""Tests for LLMConfig."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import ProviderConfig, LLMConfig


class TestLLMConfig:
    """Test LLMConfig Pydantic model."""

    def test_valid_llm(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(
            model_path="openrouter/deepseek/v4",
            provider=provider,
            default_temperature=0.2,
        )
        assert config.model_path == "openrouter/deepseek/v4"
        assert config.default_temperature == 0.2
        assert config.input_price is None
        assert config.output_price is None

    def test_llm_with_pricing(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(
            model_path="openrouter/deepseek/v4",
            provider=provider,
            input_price=2.0,
            output_price=8.0,
        )
        assert config.input_price == 2.0
        assert config.output_price == 8.0

    def test_llm_cache_prices_default_none(self):
        """Cache-tier prices default to None (fall back to the input price)."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(
            model_path="openrouter/deepseek/v4",
            provider=provider,
        )
        assert config.cache_read_price is None
        assert config.cache_write_price is None

    def test_llm_with_cache_prices(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(
            model_path="openrouter/deepseek/v4",
            provider=provider,
            input_price=2.0,
            output_price=8.0,
            cache_read_price=0.2,
            cache_write_price=2.0,
        )
        assert config.cache_read_price == 0.2
        assert config.cache_write_price == 2.0

    def test_llm_with_thinking_params(self):
        """LLMConfig accepts thinking_enabled, preserve_thinking, thinking_token_budget."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(
            model_path="hosted_vllm/qwen-3-6-27b",
            provider=provider,
            thinking_enabled=True,
            preserve_thinking=True,
            thinking_token_budget=16384,
            max_output_tokens=32768,
        )
        assert config.thinking_enabled is True
        assert config.preserve_thinking is True
        assert config.thinking_token_budget == 16384
        assert config.max_output_tokens == 32768

    def test_llm_thinking_params_default_none(self):
        """Thinking parameters default to None when not specified."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(
            model_path="openrouter/deepseek/v4",
            provider=provider,
        )
        assert config.thinking_enabled is None
        assert config.preserve_thinking is None
        assert config.thinking_token_budget is None
        assert config.max_output_tokens is None

    def test_llm_with_reasoning_effort(self):
        """LLMConfig round-trips an explicit top-level reasoning_effort (#941)."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(
            model_path="openrouter/deepseek/v4",
            provider=provider,
            reasoning_effort="low",
        )
        assert config.reasoning_effort == "low"

    def test_llm_reasoning_effort_default_none(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        assert config.reasoning_effort is None
