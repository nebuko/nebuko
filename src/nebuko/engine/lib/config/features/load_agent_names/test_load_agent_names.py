"""Tests for load_agent_names feature."""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import DEFAULT_AGENT_NAMES, load_agent_names  # noqa: E402
from config.features.load_agent_names.load_agent_names import (  # noqa: E402
    load_agent_names as load_agent_names_direct,
)


def _write_config(agents: dict) -> str:
    raw = {
        "providers": {
            "openrouter": {
                "api_url": "https://openrouter.ai/api/v1/chat/completions",
                "api_token_env": "OPENROUTER_API_KEY",
            }
        },
        "llms": {
            "test-llm": {
                "model_path": "openrouter/test/model",
                "provider": "${providers.openrouter}",
            }
        },
        "agents": {
            name: {
                "model": "${llms.test-llm}",
                "prompt_file": f".agents/prompts/{name}.md",
                "skills": [],
            }
            for name in agents
        },
    }
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(raw, f)
    f.flush()
    f.close()
    return f.name


class TestLoadAgentNames:
    def test_config_derived_roster(self):
        path = _write_config({"po": {}, "debug": {}, "auditor": {}})
        try:
            names = load_agent_names(path)
        finally:
            os.unlink(path)
        assert names == frozenset({"po", "debug", "auditor"})

    def test_empty_agents_map_falls_back(self):
        path = _write_config({})
        try:
            assert load_agent_names(path) == DEFAULT_AGENT_NAMES
        finally:
            os.unlink(path)

    def test_missing_config_falls_back(self):
        assert load_agent_names("/nonexistent/agents.yaml") == DEFAULT_AGENT_NAMES

    def test_broken_config_falls_back(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("agents: [not, a, map")
        f.flush()
        f.close()
        try:
            assert load_agent_names(f.name) == DEFAULT_AGENT_NAMES
        finally:
            os.unlink(f.name)

    def test_direct_module_matches_facade(self):
        path = _write_config({"architect": {}, "po": {}})
        try:
            assert load_agent_names_direct(path) == load_agent_names(path)
        finally:
            os.unlink(path)

    def test_env_path_used_for_default(self, monkeypatch):
        path = _write_config({"po": {}})
        monkeypatch.setenv("AGENT_CONFIG_PATH", path)
        try:
            assert load_agent_names() == frozenset({"po"})
        finally:
            os.unlink(path)

    def test_default_roster_contains_po(self):
        assert "po" in DEFAULT_AGENT_NAMES
