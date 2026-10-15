"""Single source of truth for thinking intent → API request parameters.

Thinking intent reaches the model as two carriers: the top-level SDK
``reasoning_effort`` field and ``litellm_extra_body["chat_template_kwargs"]``
(the Qwen flexible-thinking keys ``enable_thinking`` / ``preserve_thinking`` /
``reasoning_effort``). Before this module existed the mapping lived inline in
``resolve_llm_config.py`` and was therefore only reachable at config-resolution
time — so the runtime ``think`` tool could not reuse it.

``ThinkingRequest`` owns that mapping once. ``resolve_llm_config`` consumes it
statically (config → ``llm_config``) and the ``think`` tool consumes it at
runtime (intent → live ``LLM``), which is what makes "a runtime ``think`` call
is equivalent to ``thinking_enabled: true``" true by construction.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openhands.sdk import get_logger

logger = get_logger(__name__)

#: ``high`` is an alias for ``xhigh`` in the Qwen flexible-thinking set.
_REASONING_ALIASES = {"high": "xhigh"}

#: Levels the API accepts for flexible thinking.
REASONING_EFFORT_VALUES = frozenset({"xhigh", "medium", "low"})

#: The kill switch for the SDK's own ``reasoning_effort="high"`` default.
#:
#: The SDK independently defaults ``reasoning_effort`` to ``"high"`` and emits
#: it for models that support the param, which defeated
#: ``enable_thinking=false`` (issue #941). Thinking-off must therefore send an
#: explicit ``"none"``: omitting the key only hands the decision back to the SDK
#: default, and ``None`` sends nothing at all, which the provider reads as *its
#: own* default — reasoning on (issue #958).
REASONING_EFFORT_OFF = "none"


def normalize_reasoning_effort(raw: str | None) -> str | None:
    """Map a dispatch ``reasoning`` value onto the Qwen flexible-thinking set.

    Returns a normalized value (``xhigh`` / ``medium`` / ``low``), or ``None``
    when the parameter should be omitted (unset, ``none``, or unrecognized).
    ``high`` is treated as an alias for ``xhigh``.
    """
    if not raw:
        return None
    value = raw.strip().lower()
    if value == "none":
        return None
    value = _REASONING_ALIASES.get(value, value)
    if value in REASONING_EFFORT_VALUES:
        return value
    logger.warning(
        f"Unrecognized reasoning effort '{raw}'; "
        f"expected one of {sorted(REASONING_EFFORT_VALUES)}; dropping"
    )
    return None


@dataclass(frozen=True)
class ThinkingRequest:
    """Resolved thinking intent, mapped onto API request parameters."""

    #: ``chat_template_kwargs["enable_thinking"]``; ``None`` omits the key.
    enable_thinking: bool | None = None
    #: ``chat_template_kwargs["preserve_thinking"]``; ``None`` omits the key.
    preserve_thinking: bool | None = None
    #: ``chat_template_kwargs["reasoning_effort"]``; ``None`` omits the key.
    chat_template_effort: str | None = None
    #: Value for the top-level ``llm.reasoning_effort``.
    top_level_effort: str | None = None
    #: Keep the SDK's own default for the top-level effort (omit it entirely)
    #: instead of pinning the value carried by ``top_level_effort``.
    top_level_effort_default: bool = False

    @classmethod
    def resolve(
        cls,
        *,
        configured_thinking: bool | None,
        preserve_thinking: bool | None,
        explicit_effort: str | None,
        llm_reasoning: str | None,
        force_thinking: bool = False,
    ) -> "ThinkingRequest":
        """Map a resolved config intent onto request parameters.

        ``configured_thinking`` is the already precedence-resolved
        ``thinking_enabled`` (tier override > llm; ``None`` inherits the
        provider default, i.e. thinking stays on and the key is omitted).
        ``explicit_effort`` is a configured ``reasoning_effort``; otherwise the
        dispatch-side ``llm_reasoning`` (``LLM_REASONING``) is used. Effort is
        only ever emitted while thinking is on.

        ``force_thinking=True`` means "this run opted into thinking at runtime"
        and is exactly equivalent to ``configured_thinking=True``.
        """
        thinking = True if force_thinking else configured_thinking

        if thinking is not None:
            enable_thinking: bool | None = thinking
        else:
            enable_thinking = None

        chat_template_effort = None
        if thinking is not False:
            chat_template_effort = normalize_reasoning_effort(llm_reasoning)

        return cls(
            enable_thinking=enable_thinking,
            preserve_thinking=preserve_thinking,
            chat_template_effort=chat_template_effort,
            top_level_effort=explicit_effort,
        )

    @property
    def thinking_on(self) -> bool:
        """Whether thinking is on: unset means on (the key is omitted)."""
        return self.enable_thinking is not False

    def merged_chat_template_kwargs(
        self, existing: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Merge this request's thinking keys over ``existing`` kwargs.

        Keys this request does not carry are preserved, so a runtime enable can
        be applied on top of a payload the resolver already built.
        """
        kwargs: dict[str, Any] = dict(existing or {})
        if self.enable_thinking is not None:
            kwargs["enable_thinking"] = self.enable_thinking
        if self.preserve_thinking is not None:
            kwargs["preserve_thinking"] = self.preserve_thinking
        if self.chat_template_effort is not None:
            kwargs["reasoning_effort"] = self.chat_template_effort
        else:
            kwargs.pop("reasoning_effort", None)
        return kwargs

    def apply_to(self, llm: Any) -> None:
        """Apply this request in place to a live ``LLM``.

        The SDK reads both carriers off the ``LLM`` on every
        ``select_chat_options`` call, so an in-place edit takes effect on the
        next model call — no ``model_copy`` needed. If a future SDK freezes
        ``LLM`` (``pydantic`` ``model_config`` ``frozen=True``), this method is
        the single place a ``conversation.agent.model_copy(update={"llm": …})``
        fallback has to be introduced.

        Total by design: a thinking-off request also has to pin the top-level
        effort, because the SDK default (``"high"``) keeps reasoning on
        (issue #941).
        """
        extra_body = dict(getattr(llm, "litellm_extra_body", None) or {})
        existing = extra_body.get("chat_template_kwargs")
        kwargs = self.merged_chat_template_kwargs(existing)
        if kwargs:
            extra_body["chat_template_kwargs"] = kwargs
        else:
            extra_body.pop("chat_template_kwargs", None)
        llm.litellm_extra_body = extra_body or None

        if self.thinking_on:
            if self.top_level_effort_default or self.top_level_effort is None:
                llm.reasoning_effort = None
            else:
                llm.reasoning_effort = self.top_level_effort
        else:
            # #941 / #958: the SDK's own ``"high"`` default defeats
            # ``enable_thinking=false``, so thinking-off pins the kill switch.
            llm.reasoning_effort = REASONING_EFFORT_OFF
