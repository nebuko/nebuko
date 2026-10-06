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
