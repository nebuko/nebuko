"""Tests for MCPServerConfig."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import MCPServerConfig


class TestMCPServerConfig:
    """Test MCPServerConfig Pydantic model."""

    def test_defaults(self):
        config = MCPServerConfig(name="brave-search", command="npx")
        assert config.args == []
        assert config.env == {}
        assert config.transport == "stdio"

    def test_valid_stdio_server(self):
        config = MCPServerConfig(
            name="brave-search",
            command="npx",
            args=["-y", "@brave/brave-search-mcp-server@2.1.4"],
            env={"BRAVE_API_KEY": "BRAVE_API_KEY"},
        )
        assert config.command == "npx"
        assert config.env == {"BRAVE_API_KEY": "BRAVE_API_KEY"}

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError):
            MCPServerConfig(name="  ", command="npx")

    def test_empty_command_raises(self):
        with pytest.raises(ValidationError):
            MCPServerConfig(name="brave-search", command="")

    def test_secret_literal_value_is_rejected(self):
        """env values must name an env var, never embed the secret itself."""
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="brave-search",
                command="npx",
                env={"BRAVE_API_KEY": "sk-abc123"},
            )

    def test_invalid_env_key_is_rejected(self):
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="brave-search",
                command="npx",
                env={"BRAVE API KEY": "BRAVE_API_KEY"},
            )

    def test_unknown_transport_is_rejected(self):
        with pytest.raises(ValidationError):
            MCPServerConfig(name="brave-search", command="npx", transport="sse")
