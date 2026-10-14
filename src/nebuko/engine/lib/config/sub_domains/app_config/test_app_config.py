"""Tests for AppConfig."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import ProviderConfig, LLMConfig, AgentConfig, AppConfig


class TestAppConfig:
    """Test AppConfig Pydantic model."""

    def test_valid_app_config(self):
        provider = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        llm = LLMConfig(model_path="openrouter/deepseek/v4", provider=provider)
        agent = AgentConfig(
            model=llm,
            temperature=0.2,
            prompt_file=".agents/prompts/architect.md",
        )
        config = AppConfig(
            providers={"openrouter": provider},
            llms={"deepseek": llm},
            agents={"architect": agent},
        )
        assert "openrouter" in config.providers
        assert "deepseek" in config.llms
        assert "architect" in config.agents

    def test_mcp_servers_catalog_defaults_empty(self):
        """The root mcp_servers catalog is optional (issue #932)."""
        config = AppConfig(providers={}, llms={}, agents={})
        assert config.mcp_servers == {}

    def test_mcp_servers_catalog_accepts_keyed_entry(self):
        """A catalog entry keyed by name validates and is retained."""
        config = AppConfig(
            providers={},
            llms={},
            agents={},
            mcp_servers={
                "brave-search": {
                    "name": "brave-search",
                    "command": "npx",
                    "args": ["-y", "@brave/brave-search-mcp-server@2.1.4"],
                    "env": {"BRAVE_API_KEY": "BRAVE_API_KEY"},
                }
            },
        )
        assert config.mcp_servers["brave-search"].command == "npx"

    def test_mcp_servers_catalog_key_must_equal_entry_name(self):
        """A catalog key that disagrees with the entry name is rejected."""
        with pytest.raises(ValidationError, match="must equal the server's name"):
            AppConfig(
                providers={},
                llms={},
                agents={},
                mcp_servers={"brave-search": {"name": "other", "command": "npx"}},
            )
