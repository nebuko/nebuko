"""Provider-profile contract for ``nebuko init`` (issue #991)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.init.providers import (
    DEFAULT_PROVIDER_NAME,
    PROVIDER_PROFILES,
    provider_by_name,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_profiles_cover_the_shipped_templates():
    for profile in PROVIDER_PROFILES:
        assert (REPO_ROOT / profile.template_path).is_file(), profile.template_path
        assert profile.secret_names, profile.name
        assert profile.description


def test_default_provider_is_openrouter():
    assert DEFAULT_PROVIDER_NAME == "openrouter"
    assert provider_by_name("openrouter") is PROVIDER_PROFILES[0]


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("openrouter", "OPENROUTER_API_KEY"),
        ("commandcode", "COMMANDCODE_API_KEY"),
        ("opencode", "OPENCODE_GO_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
    ],
)
def test_provider_secret_names(name, secret):
    assert provider_by_name(name).secret_names == (secret,)


def test_unknown_provider_lists_the_known_ones():
    with pytest.raises(ValueError) as excinfo:
        provider_by_name("nope")
    message = str(excinfo.value)
    assert "nope" in message
    for profile in PROVIDER_PROFILES:
        assert profile.name in message
