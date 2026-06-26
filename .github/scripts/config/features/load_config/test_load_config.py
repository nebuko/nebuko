"""Tests for load_config feature."""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import load_config


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

    def test_load_config_with_thinking_params(self):
        """YAML config with thinking params loads correctly."""
        raw = {
            "providers": {
                "local_vllm": {
                    "api_url": "http://localhost:8000/v1",
                    "api_token_env": "VLLM_TOKEN",
                }
            },
            "llms": {
                "qwen3": {
                    "model_path": "hosted_vllm/qwen-3-6-27b",
                    "provider": "${providers.local_vllm}",
                    "thinking_enabled": True,
                    "preserve_thinking": True,
                    "thinking_token_budget": 16384,
                    "max_output_tokens": 32768,
                }
            },
            "agents": {
                "dev": {
                    "model": "${llms.qwen3}",
                    "temperature": 0.0,
                    "prompt_file": ".agents/prompts/dev.md",
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

        llm = config.llms["qwen3"]
        assert llm.thinking_enabled is True
        assert llm.preserve_thinking is True
        assert llm.thinking_token_budget == 16384
        assert llm.max_output_tokens == 32768

        # Verify agent references the LLM correctly
        agent = config.agents["dev"]
        assert agent.model_ref.thinking_enabled is True
        assert agent.model_ref.max_output_tokens == 32768

        os.unlink(f.name)

    def test_load_real_config_with_thinking_params(self):
        """Load the actual agent-config.yaml and verify Qwen3.6 models have thinking params."""
        config_path = str(Path(__file__).parents[3] / "agent-config.yaml")
        if not Path(config_path).exists():
            pytest.skip(f"Config file not found: {config_path}")
        config = load_config(config_path)

        qwen27b = config.llms.get("qwen-3-6-27b")
        if qwen27b:
            assert qwen27b.thinking_enabled is True
            assert qwen27b.preserve_thinking is True
            assert qwen27b.max_output_tokens == 32768
