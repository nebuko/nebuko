"""The shipped LLM provider profiles (issue #991).

Each profile names the ``defaults/agents/agent-config-<provider>.yaml`` template
the client's config is generated from and the secrets that template's
``api_token_env`` entries reference.
"""

from __future__ import annotations

from nebuko.engine.init.ports import ProviderProfile


class OpenRouterProfile(ProviderProfile):
    name = "openrouter"
    description = "OpenRouter (default)"
    template_path = "defaults/agents/agent-config-openrouter.yaml"
    secret_names = ("OPENROUTER_API_KEY",)


class CommandCodeProfile(ProviderProfile):
    name = "commandcode"
    description = "Command Code"
    template_path = "defaults/agents/agent-config-commandcode.yaml"
    secret_names = ("COMMANDCODE_API_KEY",)


class OpenCodeProfile(ProviderProfile):
    name = "opencode"
    description = "OpenCode Go"
    template_path = "defaults/agents/agent-config-opencode.yaml"
    secret_names = ("OPENCODE_GO_API_KEY",)


class MistralProfile(ProviderProfile):
    name = "mistral"
    description = "Mistral"
    template_path = "defaults/agents/agent-config-mistral.yaml"
    secret_names = ("MISTRAL_API_KEY",)


PROVIDER_PROFILES: tuple[ProviderProfile, ...] = (
    OpenRouterProfile(),
    CommandCodeProfile(),
    OpenCodeProfile(),
    MistralProfile(),
)

DEFAULT_PROVIDER_NAME = PROVIDER_PROFILES[0].name


# @stub-protected
def provider_by_name(name: str) -> ProviderProfile:
    """Return the profile called ``name``; raise ``ValueError`` when unknown."""
    for profile in PROVIDER_PROFILES:
        if profile.name == name:
            return profile
    known = ", ".join(profile.name for profile in PROVIDER_PROFILES)
    raise ValueError(f"unknown provider {name!r} (known: {known})")
