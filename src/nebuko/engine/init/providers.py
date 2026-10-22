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


class ClaudeCodeProfile(ProviderProfile):
    name = "claude-code"
    description = "Claude Code (subscription token or API key, runs through ACP)"
    template_path = "defaults/agents/agent-config-claude-code.yaml"
    secret_names = ("CLAUDE_CODE_OAUTH_TOKEN",)


class LocalRunnerClaudeCodeProfile(ClaudeCodeProfile):
    """Claude Code on the user's own runner: the runner holds the token.

    Not offered by ``--provider``: ``nebuko init anthropic-local-runner`` selects
    it, because the OAuth token lives in the runner's ``.env`` and must never be
    stored as a GitHub secret.
    """

    name = "claude-code-local-runner"
    description = "Claude Code on a self-hosted runner (token stays on the runner)"
    secret_names = ()


LOCAL_RUNNER_PROFILE = LocalRunnerClaudeCodeProfile()

PROVIDER_PROFILES: tuple[ProviderProfile, ...] = (
    OpenRouterProfile(),
    CommandCodeProfile(),
    OpenCodeProfile(),
    MistralProfile(),
    ClaudeCodeProfile(),
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
