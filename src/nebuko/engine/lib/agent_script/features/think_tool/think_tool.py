"""``think`` tool with a side effect: latch API-level thinking on.

The SDK's builtin ``ThinkTool`` only logs a thought — it never touches the
conversation, so an agent whose LLM resolves ``thinking_enabled: false`` cannot
opt into reasoning mid-run (issue #937). This engine-owned replacement keeps the
same wire schema (name ``think``, ``ThinkAction`` / ``ThinkObservation``) but
its executor applies a :class:`ThinkingRequest` to the *live* ``LLM`` reached
through ``conversation.agent.llm``.

The SDK rebuilds the request options from the ``LLM`` on every model call, so
the latch takes effect on the next call and stays on for the rest of the run —
the runtime counterpart of ``thinking_enabled: true``.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Self

from openhands.sdk import get_logger
from openhands.sdk.llm.options.chat_options import select_chat_options
from openhands.sdk.tool.builtins.think import (
    THINK_DESCRIPTION,
    ThinkAction,
    ThinkObservation,
)
from openhands.sdk.tool.tool import ToolAnnotations, ToolDefinition, ToolExecutor

from agent_script.sub_domains.thinking_request.thinking_request import (
    ThinkingRequest,
)

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)

#: Config-facing tool name. The SDK derives the default name from the class
#: name (``ThinkEnableTool`` -> ``think_enable``), and the builtin exposes
#: ``think``; the name must be pinned or the agent silently loses the tool.
TOOL_NAME = "think"

#: Env var carrying the dispatch-side reasoning level, same source the
#: resolver reads.
_LLM_REASONING_ENV = "LLM_REASONING"


class ThinkEnableExecutor(ToolExecutor):
    """Latch thinking on for the rest of the run on the live ``LLM``."""

    def __call__(
        self,
        _action: ThinkAction,
        conversation: "BaseConversation | None" = None,
    ) -> ThinkObservation:
        llm = _live_llm(conversation)
        if llm is None:
            # Loud, never a silent drop (issue #864): an unwired ``think`` call
            # must be indistinguishable from a failure, not from success.
            message = (
                "think: could not reach the live LLM (no conversation, or "
                "conversation.agent.llm is missing) — thinking was NOT enabled."
            )
            logger.error(message)
            return ThinkObservation.from_text(text=message, is_error=True)

        request = ThinkingRequest.resolve(
            configured_thinking=None,
            preserve_thinking=_current_preserve_thinking(llm),
            explicit_effort=None,
            llm_reasoning=_env_reasoning(),
            force_thinking=True,
        )
        request.apply_to(llm)

        if not _thinking_observable(llm):
            message = (
                "think: reasoning parameters were applied to the LLM but none "
                "reaches the request payload for this model — thinking may be "
                "ignored. Check the LLM's reasoning support."
            )
            logger.error(message)
            return ThinkObservation.from_text(text=message, is_error=True)

        logger.info("think: API-level thinking latched on for the rest of the run")
        return ThinkObservation.from_text(
            text=(
                "Your thought has been logged. API-level thinking is now "
                "enabled for the rest of this run; further think calls are "
                "idempotent."
            )
        )


def _live_llm(conversation: "BaseConversation | None") -> Any | None:
    """Return ``conversation.agent.llm`` when reachable, else ``None``."""
    agent = getattr(conversation, "agent", None)
    return getattr(agent, "llm", None)


def _current_preserve_thinking(llm: Any) -> bool | None:
    """Carry the ``preserve_thinking`` already on the payload through the latch."""
    extra_body = getattr(llm, "litellm_extra_body", None) or {}
    kwargs = extra_body.get("chat_template_kwargs") or {}
    return kwargs.get("preserve_thinking")


def _env_reasoning() -> str | None:
    import os

    return os.getenv(_LLM_REASONING_ENV)


def _thinking_observable(llm: Any) -> bool:
    """Whether an observable reasoning carrier landed in the request payload."""
    options = select_chat_options(llm, {}, has_tools=True)
    extra_body = options.get("extra_body") or {}
    kwargs = extra_body.get("chat_template_kwargs") or {}
    return (
        options.get("reasoning_effort") not in (None, "", "none")
        or kwargs.get("enable_thinking") is True
    )


class ThinkEnableTool(ToolDefinition[ThinkAction, ThinkObservation]):
    """``think`` tool that enables API-level thinking as a side effect."""

    name: ClassVar[str] = TOOL_NAME

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params: Any,
    ) -> Sequence[Self]:
        """Create the tool.

        Args:
            conv_state: Optional conversation state (unused).
            **params: Additional parameters (none supported).

        Raises:
            ValueError: If any parameters are provided.
        """
        if params:
            raise ValueError("ThinkEnableTool doesn't accept parameters")
        return [
            cls(
                description=THINK_DESCRIPTION,
                action_type=ThinkAction,
                observation_type=ThinkObservation,
                executor=ThinkEnableExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
