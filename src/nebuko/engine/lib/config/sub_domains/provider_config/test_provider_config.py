"""Tests for ProviderConfig."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import ProviderConfig


class TestProviderConfig:
    """Test ProviderConfig Pydantic model."""

    def test_valid_provider(self):
        config = ProviderConfig(api_url="https://example.com", api_token_env="MY_TOKEN")
        assert config.api_url == "https://example.com"
        assert config.api_token_env == "MY_TOKEN"

    def test_missing_api_url_raises(self):
        with pytest.raises(ValidationError):
            ProviderConfig(api_token_env="MY_TOKEN")


class TestProviderConfigIdentityShape:
    """The static client-identity shape — names only, never per-issue values."""

    def test_identity_fields_are_optional_and_default_empty(self):
        config = ProviderConfig(api_url="https://example.com", api_token_env="T")
        assert config.user_agent is None
        assert config.session_header is None
        assert config.extra_headers == {}

    def test_accepts_the_static_client_identity_shape(self):
        config = ProviderConfig(
            api_url="https://example.com",
            api_token_env="T",
            user_agent="nebuko/1.0",
            session_header="x-opencode-session",
            extra_headers={"X-Static": "1"},
        )
        assert config.user_agent == "nebuko/1.0"
        assert config.session_header == "x-opencode-session"
        assert config.extra_headers == {"X-Static": "1"}

    @pytest.mark.parametrize(
        "bad_name", ["", "x session", "x-session:", "x\tsession", "bad\nname"]
    )
    def test_rejects_a_malformed_session_header_name(self, bad_name):
        with pytest.raises(ValidationError):
            ProviderConfig(
                api_url="https://example.com",
                api_token_env="T",
                session_header=bad_name,
            )

    @pytest.mark.parametrize("bad_name", ["", "x session", "x-session:", "a:b"])
    def test_rejects_a_malformed_extra_headers_key(self, bad_name):
        with pytest.raises(ValidationError):
            ProviderConfig(
                api_url="https://example.com",
                api_token_env="T",
                extra_headers={bad_name: "v"},
            )
