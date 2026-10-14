"""Tests for resolve_refs feature."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import resolve_refs


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
            "agents": {"architect": {"model": "${providers.openrouter}"}},
        }
        result = resolve_refs(root)
        assert result["agents"]["architect"]["model"] == {
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
            "agents": {"architect": {"model": "${llms.deepseek}"}},
        }
        result = resolve_refs(root)
        model = result["agents"]["architect"]["model"]
        assert model["model_path"] == "openrouter/deepseek/v4"
        assert model["provider"]["api_url"] == "https://example.com"

    def test_partial_inline_substitution(self):
        """Partial ${ref} in a string is substituted with the value."""
        root = {
            "providers": {"openrouter": {"api_url": "https://example.com"}},
            "agents": {"architect": {"url": "prefix_${providers.openrouter.api_url}_suffix"}},
        }
        result = resolve_refs(root)
        assert result["agents"]["architect"]["url"] == "prefix_https://example.com_suffix"

    def test_list_resolution(self):
        """Lists are traversed and refs resolved."""
        root = {
            "providers": {"openrouter": {"api_url": "https://example.com"}},
            "agents": {
                "architect": {
                    "skills": ["${providers.openrouter.api_url}"],
                }
            },
        }
        result = resolve_refs(root)
        assert result["agents"]["architect"]["skills"] == ["https://example.com"]

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
            "agents": {"architect": {"model": "${llms.deepseek-v4-pro}"}},
        }
        result = resolve_refs(root)
        assert result["agents"]["architect"]["model"]["model_path"] == "openrouter/deepseek/v4"

    def test_mcp_servers_catalog_ref_resolves_to_entry(self):
        """${mcp_servers.<name>} resolves against a name-keyed root catalog.

        The catalog must be a *mapping*: a list-rooted ref cannot be walked by
        the dotted-path resolver (issue #932).
        """
        root = {
            "mcp_servers": {
                "brave-search": {
                    "name": "brave-search",
                    "command": "npx",
                    "args": ["-y", "@brave/brave-search-mcp-server@2.1.4"],
                    "env": {"BRAVE_API_KEY": "BRAVE_API_KEY"},
                }
            },
            "agents": {
                "context": {"mcp_servers": ["${mcp_servers.brave-search}"]},
            },
        }
        result = resolve_refs(root)
        assert result["agents"]["context"]["mcp_servers"] == [
            {
                "name": "brave-search",
                "command": "npx",
                "args": ["-y", "@brave/brave-search-mcp-server@2.1.4"],
                "env": {"BRAVE_API_KEY": "BRAVE_API_KEY"},
            }
        ]

    def test_non_string_values_unchanged(self):
        """Non-string, non-dict, non-list values pass through unchanged."""
        config = {"count": 42, "flag": True, "nothing": None}
        result = resolve_refs(config)
        assert result == config
