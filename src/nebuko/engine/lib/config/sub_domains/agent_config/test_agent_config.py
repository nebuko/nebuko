"""Tests for AgentConfig."""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import ProviderConfig, LLMConfig, AgentConfig, SkillSpec, load_config


class TestAgentConfig:
    """Test AgentConfig Pydantic model."""

    def test_valid_agent(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            temperature=0.2,
            prompt_file=".agents/prompts/architect.md",
            skills=["python"],
        )
        assert agent.temperature == 0.2
        assert agent.skills == [SkillSpec(mode="inject", names=["python"])]

    def test_default_tools(self):
        """AgentConfig defaults to full tool set."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/architect.md",
        )
        assert agent.tools == ["terminal", "file_editor", "task_tracker", "think"]

    def test_mcp_servers_default_empty(self):
        """AgentConfig defaults mcp_servers to an empty list."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/context.md",
        )
        assert agent.mcp_servers == []

    def test_load_config_with_mcp_servers(self):
        """load_config parses the mcp_servers stanza from YAML."""
        raw = {
            "providers": {
                "openrouter": {
                    "api_url": "https://openrouter.ai/api/v1",
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
                "context": {
                    "model": "${llms.deepseek}",
                    "prompt_file": ".agents/prompts/context.md",
                    "mcp_servers": [
                        {
                            "name": "brave-search",
                            "command": "npx",
                            "args": ["-y", "@brave/brave-search-mcp-server@2.1.4"],
                            "env": {"BRAVE_API_KEY": "BRAVE_API_KEY"},
                        }
                    ],
                },
                "dev": {
                    "model": "${llms.deepseek}",
                    "prompt_file": ".agents/prompts/dev.md",
                },
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            config = load_config(f.name)

        assert config.agents["dev"].mcp_servers == []
        server = config.agents["context"].mcp_servers[0]
        assert server.name == "brave-search"
        assert server.command == "npx"
        assert server.env == {"BRAVE_API_KEY": "BRAVE_API_KEY"}

        os.unlink(f.name)

    def test_load_config_with_mcp_catalog_ref(self):
        """A ${mcp_servers.<name>} ref resolves to the catalog entry (issue #932)."""
        raw = {
            "providers": {
                "openrouter": {
                    "api_url": "https://openrouter.ai/api/v1",
                    "api_token_env": "OPENROUTER_API_KEY",
                }
            },
            "llms": {
                "deepseek": {
                    "model_path": "openrouter/deepseek/v4",
                    "provider": "${providers.openrouter}",
                }
            },
            "mcp_servers": {
                "brave-search": {
                    "name": "brave-search",
                    "command": "npx",
                    "args": ["-y", "@brave/brave-search-mcp-server@2.1.4"],
                    "env": {"BRAVE_API_KEY": "BRAVE_API_KEY"},
                }
            },
            "agents": {
                "context": {
                    "model": "${llms.deepseek}",
                    "prompt_file": ".agents/prompts/context.md",
                    "mcp_servers": ["${mcp_servers.brave-search}"],
                },
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(raw, f)
            f.flush()
            config = load_config(f.name)

        assert list(config.mcp_servers) == ["brave-search"]
        server = config.agents["context"].mcp_servers[0]
        assert server.name == "brave-search"
        assert server.command == "npx"

        os.unlink(f.name)

    def test_explicit_tools(self):
        """AgentConfig accepts an explicit tools list."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/context.md",
            tools=["terminal", "think"],
        )
        assert agent.tools == ["terminal", "think"]

    def test_empty_tools(self):
        """AgentConfig accepts an empty tools list."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/architect.md",
            tools=[],
        )
        assert agent.tools == []

    def test_restricted_tools_architect_pattern(self):
        """Architect/issue pattern: think only."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/architect.md",
            tools=["think"],
        )
        assert agent.tools == ["think"]

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
                "architect": {
                    "model": "${llms.deepseek}",
                    "temperature": 0.2,
                    "prompt_file": ".agents/prompts/architect.md",
                    "skills": [],
                    "tools": ["think"],
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

        assert config.agents["architect"].tools == ["think"]
        assert config.agents["dev"].tools == [
            "terminal", "file_editor", "task_tracker", "think",
        ]

        os.unlink(f.name)

    def test_complexity_overrides_default_empty(self):
        """AgentConfig defaults complexity_overrides to an empty dict."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/architect.md",
        )
        assert agent.complexity_overrides == {}

    def test_complexity_overrides_accepts_tiers(self):
        """AgentConfig accepts low/medium/high tier overrides."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            prompt_file=".agents/prompts/architect.md",
            complexity_overrides={
                "low": {"temperature": 0.0},
                "high": {"temperature": 0.9, "reasoning": "xhigh"},
            },
        )
        assert set(agent.complexity_overrides) == {"low", "high"}
        assert agent.complexity_overrides["high"].reasoning == "xhigh"
        assert agent.complexity_overrides["high"].temperature == 0.9

    def test_complexity_overrides_rejects_invalid_tier(self):
        """An unknown tier key (not low/medium/high) is rejected by Literal validation."""
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        with pytest.raises(ValidationError):
            AgentConfig(
                model=llm,
                prompt_file=".agents/prompts/architect.md",
                complexity_overrides={"critical": {"temperature": 0.9}},
            )
