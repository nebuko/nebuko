"""Tests for the layered config system (config.py)."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from config import (
    AppConfig,
    AgentConfig,
    LLMConfig,
    ProviderConfig,
    resolve_refs,
    load_config,
)


# ── ${ref} Resolver Tests ────────────────────────────────────────────


class TestResolveRefs:
    """Test the ${ref} resolver function."""

    def test_no_refs_returns_unchanged(self):
        """Dict without any ${ref} strings is returned as-is."""
        config = {"key": "value", "nested": {"a": 1, "b": 2}}
        result = resolve_refs(config)
        assert result == config

    def test_full_string_ref_resolves_to_object(self):
        """Full-string ${providers.x} resolves to the referenced dict."""
        root = {
            "providers": {"openrouter": {"api_url": "https://example.com"}},
            "agents": {"genius": {"model": "${providers.openrouter}"}},
        }
        result = resolve_refs(root)
        assert result["agents"]["genius"]["model"] == {
            "api_url": "https://example.com"
        }

    def test_full_string_ref_resolves_nested(self):
        """${llms.x} resolves to LLM config dict which itself has resolved provider."""
        root = {
            "providers": {"openrouter": {"api_url": "https://example.com"}},
            "llms": {
                "deepseek": {
                    "model_path": "openrouter/deepseek/v4",
                    "provider": "${providers.openrouter}",
                }
            },
            "agents": {"genius": {"model": "${llms.deepseek}"}},
        }
        result = resolve_refs(root)
        model = result["agents"]["genius"]["model"]
        assert model["model_path"] == "openrouter/deepseek/v4"
        assert model["provider"]["api_url"] == "https://example.com"

    def test_partial_inline_substitution(self):
        """Partial ${ref} in a string is substituted with the value."""
        root = {
            "providers": {"openrouter": {"api_url": "https://example.com"}},
            "agents": {"genius": {"url": "prefix_${providers.openrouter.api_url}_suffix"}},
        }
        result = resolve_refs(root)
        assert result["agents"]["genius"]["url"] == "prefix_https://example.com_suffix"

    def test_list_resolution(self):
        """Lists are traversed and refs resolved."""
        root = {
            "providers": {"openrouter": {"api_url": "https://example.com"}},
            "agents": {
                "genius": {
                    "skills": ["${providers.openrouter.api_url}"],
                }
            },
        }
        result = resolve_refs(root)
        assert result["agents"]["genius"]["skills"] == ["https://example.com"]

    def test_hyphenated_keys_in_refs(self):
        """Refs with hyphens in path keys (e.g., deepseek-v4-pro) are resolved."""
        root = {
            "providers": {"openrouter": {"api_url": "https://example.com"}},
            "llms": {
                "deepseek-v4-pro": {
                    "model_path": "openrouter/deepseek/v4",
                    "provider": "${providers.openrouter}",
                }
            },
            "agents": {"genius": {"model": "${llms.deepseek-v4-pro}"}},
        }
        result = resolve_refs(root)
        assert result["agents"]["genius"]["model"]["model_path"] == "openrouter/deepseek/v4"

    def test_non_string_values_unchanged(self):
        """Non-string, non-dict, non-list values pass through unchanged."""
        config = {"count": 42, "flag": True, "nothing": None}
        result = resolve_refs(config)
        assert result == config


# ── Pydantic Model Tests ─────────────────────────────────────────────


class TestProviderConfig:
    """Test ProviderConfig Pydantic model."""

    def test_valid_provider(self):
        config = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        assert config.api_url == "https://example.com"
        assert config.api_token_env == "MY_TOKEN"

    def test_missing_api_url_raises(self):
        with pytest.raises(ValidationError):
            ProviderConfig(api_token_env="MY_TOKEN")


class TestLLMConfig:
    """Test LLMConfig Pydantic model."""

    def test_valid_llm(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        config = LLMConfig(
            model_path="openrouter/deepseek/v4",
            provider=provider,  # alias="provider" for provider_ref
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
            provider=provider,  # alias="provider" for provider_ref
            input_price=2.0,
            output_price=8.0,
        )
        assert config.input_price == 2.0
        assert config.output_price == 8.0


class TestAgentConfig:
    """Test AgentConfig Pydantic model."""

    def test_valid_agent(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,  # alias="model" for model_ref
            temperature=0.2,
            prompt_file=".agents/prompts/genius.md",
            skills=["python"],
        )
        assert agent.temperature == 0.2
        assert agent.skills == ["python"]

    def test_default_tools(self):
        """AgentConfig defaults to full tool set."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/genius.md",
        )
        assert agent.tools == ["terminal", "file_editor", "task_tracker", "think", "finish"]

    def test_explicit_tools(self):
        """AgentConfig accepts an explicit tools list."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/ctx.md",
            tools=["terminal", "think", "finish"],
        )
        assert agent.tools == ["terminal", "think", "finish"]

    def test_empty_tools(self):
        """AgentConfig accepts an empty tools list."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/genius.md",
            tools=[],
        )
        assert agent.tools == []

    def test_restricted_tools_genius_pattern(self):
        """Genius/issue pattern: think + finish only."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/genius.md",
            tools=["think", "finish"],
        )
        assert agent.tools == ["think", "finish"]

    def test_load_config_with_tools(self):
        """load_config parses tools field from YAML correctly."""
        raw = {
            "providers": {
                "openrouter": {
                    "api_url": "https://openrouter.ai/api/v1/chat/completions",
                    "api_token_env": "OPENROUTER_API_KEY",
                }
            },
            "llms": {
                "deepseek": {
                    "model_path": "openrouter/deepseek/v4",
                    "provider": "${providers.openrouter}",
                }
            },
            "agents": {
                "genius": {
                    "model": "${llms.deepseek}",
                    "temperature": 0.2,
                    "prompt_file": ".agents/prompts/genius.md",
                    "skills": [],
                    "tools": ["think", "finish"],
                },
                "dev": {
                    "model": "${llms.deepseek}",
                    "prompt_file": ".agents/prompts/dev.md",
                    "skills": [],
                    # No tools field — should use default
                },
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            config = load_config(f.name)

        assert config.agents["genius"].tools == ["think", "finish"]
        # dev has no tools field, so it gets the default
        assert config.agents["dev"].tools == [
            "terminal", "file_editor", "task_tracker", "think", "finish",
        ]

        os.unlink(f.name)


class TestAppConfig:
    """Test AppConfig Pydantic model."""

    def test_valid_app_config(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,  # alias="model" for model_ref
            temperature=0.2,
            prompt_file=".agents/prompts/genius.md",
        )
        config = AppConfig(
            providers={"openrouter": provider},
            llms={"deepseek": llm},
            agents={"genius": agent},
        )
        assert "openrouter" in config.providers
        assert "deepseek" in config.llms
        assert "genius" in config.agents


# ── load_config Integration Tests ────────────────────────────────────


class TestLoadConfig:
    """Test the load_config helper function."""

    def test_load_valid_config(self):
        """Load a valid config file and validate all layers."""
        raw = {
            "providers": {
                "openrouter": {
                    "api_url": "https://openrouter.ai/api/v1/chat/completions",
                    "api_token_env": "OPENROUTER_API_KEY",
                }
            },
            "llms": {
                "deepseek-v4-pro": {
                    "model_path": "openrouter/deepseek/deepseek-v4-pro",
                    "provider": "${providers.openrouter}",
                    "default_temperature": 0.2,
                }
            },
            "agents": {
                "genius": {
                    "model": "${llms.deepseek-v4-pro}",
                    "temperature": 0.2,
                    "prompt_file": ".agents/prompts/genius.md",
                    "skills": [],
                }
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            config = load_config(f.name)

        assert "openrouter" in config.providers
        assert "deepseek-v4-pro" in config.llms
        assert "genius" in config.agents
        assert config.agents["genius"].model_ref.model_path == "openrouter/deepseek/deepseek-v4-pro"
        assert config.agents["genius"].model_ref.provider_ref.api_url == "https://openrouter.ai/api/v1/chat/completions"

        os.unlink(f.name)

    def test_load_missing_file_raises(self):
        """Loading a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_load_invalid_yaml_raises(self):
        """Invalid YAML content raises ValueError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("invalid: yaml: [")
            f.flush()
            with pytest.raises(Exception):  # yaml.YAMLError
                load_config(f.name)
            os.unlink(f.name)

    def test_load_missing_section_raises(self):
        """Config missing required section raises ValidationError."""
        raw = {
            "providers": {
                "openrouter": {
                    "api_url": "https://example.com",
                    "api_token_env": "MY_TOKEN",
                }
            },
            # Missing llms and agents
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            with pytest.raises(ValidationError):
                load_config(f.name)
            os.unlink(f.name)
