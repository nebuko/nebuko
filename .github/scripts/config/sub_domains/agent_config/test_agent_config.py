"""Tests for AgentConfig."""

import os
import tempfile

import pytest
import yaml

from config import ProviderConfig, LLMConfig, AgentConfig, load_config


class TestAgentConfig:
    """Test AgentConfig Pydantic model."""

    def test_valid_agent(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
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
        assert config.agents["dev"].tools == [
            "terminal", "file_editor", "task_tracker", "think", "finish",
        ]

        os.unlink(f.name)
