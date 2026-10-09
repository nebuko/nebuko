"""Tests for AppConfig."""

import os
import sys
from pathlib import Path

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
