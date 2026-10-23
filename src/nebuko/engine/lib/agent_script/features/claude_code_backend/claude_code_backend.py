"""Claude Code backend — an ACP session in place of the SDK ``Agent`` loop.

``backend: claude-code`` on an agent role swaps the model-driven SDK loop for the
SDK's ``ACPAgent``: Claude Code runs the whole turn (its own tools, context
management and model calls) and the conversation, supervision and publication
machinery around it stay Nebuko's. Claude Code has none of Nebuko's tools, so
the verdict and report are collected by the engine's closing turn instead
(``nebuko.engine.acp_finalize``).
"""

from __future__ import annotations

from typing import Any

from openhands.sdk import get_logger

logger = get_logger(__name__)

BACKEND_NAME = "claude-code"

#: Key of the Claude Code entry in the SDK's ACP provider registry.
_ACP_PROVIDER_KEY = "claude-code"

#: Prepended to the role prompt: the role instructions were written for the SDK
#: agent's tool surface, which Claude Code does not have.
ENGINE_PREAMBLE = """\
# Running inside Claude Code

You are running as Claude Code, not as the OpenHands SDK agent these role \
instructions were written for. Use your own tools (Bash, Read, Edit, ...) \
wherever the instructions name `terminal`, `file_editor`, `task_tracker` or \
`think`. Do NOT look for a `finalize` tool: when the work is done, stop and \
write your final answer as plain text. The engine then asks you for your \
report and turn outcome in one closing turn.

"""


def acp_model_from_model_path(model_path: str | None) -> str | None:
    """The Claude Code model alias named by an LLM ``model_path``.

    The config keeps the usual ``<provider>/<model>`` shape; only the last
    segment is the alias Claude Code accepts (``sonnet``, ``opus[1m]``,
    ``claude-opus-5``). ``None`` leaves Claude Code on its own default.
    """
    if not model_path:
        return None
    alias = model_path.rsplit("/", 1)[-1].strip()
    return alias or None


def with_engine_preamble(prompt: str) -> str:
    """The role prompt prefixed with the Claude Code adaptation notice."""
    return f"{ENGINE_PREAMBLE}{prompt}"


def is_claude_code_agent(agent: Any) -> bool:
    """Whether ``agent`` is the ACP agent this backend builds.

    Matched on the class name so callers (and test doubles) need not import the
    SDK's ACP stack.
    """
    return type(agent).__name__ == "ACPAgent"


def build_claude_code_agent(model_path: str | None, agent_context: Any = None) -> Any:
    """Build the ``ACPAgent`` that launches Claude Code through the SDK registry.

    The launch command, the permission-bypass session mode and the credential
    env vars (``CLAUDE_CODE_OAUTH_TOKEN`` / ``ANTHROPIC_API_KEY``, read from the
    process environment) all come from the SDK's provider entry.
    """
    from openhands.sdk.agent import ACPAgent
    from openhands.sdk.settings.acp_providers import get_acp_provider

    provider = get_acp_provider(_ACP_PROVIDER_KEY)
    if provider is None:
        raise RuntimeError(
            f"the installed OpenHands SDK has no '{_ACP_PROVIDER_KEY}' ACP provider"
        )
    acp_model = acp_model_from_model_path(model_path)
    logger.info(f"Building Claude Code ACP agent (model={acp_model or 'default'})")
    return ACPAgent(
        acp_command=list(provider.default_command),
        acp_server=provider.key,
        acp_model=acp_model,
        agent_context=agent_context,
    )
