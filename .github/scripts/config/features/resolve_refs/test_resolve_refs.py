"""Tests for resolve_refs feature."""

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
