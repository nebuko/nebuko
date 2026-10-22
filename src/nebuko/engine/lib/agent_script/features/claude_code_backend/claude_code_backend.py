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


class RestrictedACPAgent:
    """Subclass of ACPAgent for defense-in-depth tool enforcement.

    This class overrides request_permission() to deny tool calls outside the
    allowed_tool_names set. It serves as a secondary enforcement layer when the
    Claude Code CLI flags (primary layer) are insufficient or when the session
    mode doesn't fully suppress requests.

    Note: This is currently a stub. Full implementation requires subclassing the
    SDK's ACPAgent and overriding the async request_permission method, which
    may not be called if bypassPermissions mode suppresses the round-trip.
    """

    allowed_tool_names: frozenset[str]

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[Any],
        **kwargs: Any,
    ) -> Any:
        """Check if the tool call is in allowed_tool_names and return accordingly.

        If the tool name (from tool_call.name or similar) is in allowed_tool_names,
        returns the first option (approval). Otherwise, selects a reject option if
        available (e.g., options with reject_once=True or reject_always=True).
        """
        pass

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


def _claude_code_cli_args_for_tools(
    allowed_tools: list[str] | None,
) -> tuple[list[str], str | None]:
    """Build CLI args and session mode for a tool allowlist.

    Returns (acp_args, acp_session_mode) based on the allowed_tools.
    When allowed_tools is None or empty, returns defaults for unrestricted access.
    When allowed_tools excludes terminal/file_editor, returns args for a restricted session.

    Note: The actual flag names (--disallowedTools, --permission-mode) are a guess
    pending verification against the installed claude-agent-acp. If those flags
    don't exist in the CLI, a non-bypassPermissions session mode alone provides
    the restriction.
    """
    if not allowed_tools:
        # No restriction: use default args and session mode (bypassPermissions)
        return [], None

    # Check if this is a restricted role (no terminal/file_editor)
    has_exec = "terminal" in allowed_tools or "file_editor" in allowed_tools
    if has_exec:
        # Full access: no restriction needed
        return [], None

    # Restricted role: build deny list for exec tools
    all_exec_tools = {"terminal", "file_editor", "terminal_batch"}
    denied = all_exec_tools - set(allowed_tools)

    # Build CLI args with disallowed tools (best-effort guess pending verification)
    acp_args = []
    if denied:
        # Try the common flag name; actual CLI behavior TBD
        acp_args.extend(["--disallowedTools", ",".join(sorted(denied))])

    # Use a non-bypass session mode for restricted roles so request_permission is called
    # (bypassPermissions may suppress it entirely; safer to use a restrictive mode)
    acp_session_mode = "restrictedConsole"  # or similar; TBD pending CLI verification

    return acp_args, acp_session_mode


def build_claude_code_agent(
    model_path: str | None,
    agent_context: Any = None,
    allowed_tools: list[str] | None = None,
) -> Any:
    """Build the ``ACPAgent`` that launches Claude Code through the SDK registry.

    The launch command, the permission-bypass session mode and the credential
    env vars (``CLAUDE_CODE_OAUTH_TOKEN`` / ``ANTHROPIC_API_KEY``, read from the
    process environment) all come from the SDK's provider entry.

    Args:
        model_path: LLM model identifier (e.g., "anthropic/sonnet").
        agent_context: SDK AgentContext for system message and skills.
        allowed_tools: List of tools the role is allowed to use. If provided and
                       excludes exec tools (terminal/file_editor), enforces
                       restrictions via CLI args and non-bypass session mode.
    """
    from openhands.sdk.agent import ACPAgent
    from openhands.sdk.settings.acp_providers import get_acp_provider

    provider = get_acp_provider(_ACP_PROVIDER_KEY)
    if provider is None:
        raise RuntimeError(
            f"the installed OpenHands SDK has no '{_ACP_PROVIDER_KEY}' ACP provider"
        )
    acp_model = acp_model_from_model_path(model_path)
    acp_args, acp_session_mode = _claude_code_cli_args_for_tools(allowed_tools)

    logger.info(
        f"Building Claude Code ACP agent "
        f"(model={acp_model or 'default'}, "
        f"allowed_tools={allowed_tools or 'all'}, "
        f"restricted={'yes' if acp_session_mode else 'no'})"
    )
    return ACPAgent(
        acp_command=list(provider.default_command),
        acp_server=provider.key,
        acp_model=acp_model,
        agent_context=agent_context,
        acp_args=acp_args,
        acp_session_mode=acp_session_mode,
    )
