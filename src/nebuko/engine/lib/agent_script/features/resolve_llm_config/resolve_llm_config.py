"""Resolve LLM config feature — config file parsing, env fallback, and override handling."""

import os

import litellm
from litellm.integrations.custom_logger import CustomLogger
from openhands.sdk import get_logger

from agent_script.features.resolve_provider_runtime.resolve_provider_runtime import (
    build_provider_runtime,
)
from agent_script.sub_domains.metrics.cost import CostRates

logger = get_logger(__name__)

# Qwen3.8 flexible-thinking vocabulary, delivered via chat_template_kwargs.
REASONING_EFFORT_VALUES = ("xhigh", "medium", "low")
# Aliases mapped onto the model vocabulary (e.g. OpenAI-style "high").
_REASONING_ALIASES = {"high": "xhigh"}

# A high-reasoning turn can emit no bytes for minutes while the model thinks;
# the SDK default (300s) aborts the HTTP call mid-reasoning and loses the turn
# (issue #775). The SDK derives ``stream_idle_timeout`` from this value, so a
# streamed response gets the same headroom between chunks.
LLM_TIMEOUT_S = 900


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


def resolve_api_url(raw: str) -> str:
    """Resolve a provider ``api_url`` value to a concrete URL.

    If the value already looks like an ``http://`` or ``https://`` URL it is
    returned unchanged. Otherwise it is treated as an environment variable
    name (e.g. ``LOCAL_VLLM_API_URL``) and the variable's value is returned —
    mirroring how ``api_token_env`` is resolved.

    Raises:
        ValueError: If the referenced env var is unset/empty, or its value is
            not an ``http://``/``https://`` URL.
    """
    value = raw.strip()
    if value.startswith(("http://", "https://")):
        return value
    resolved = os.environ.get(value)
    if not resolved:
        raise ValueError(
            f"api_url '{value}' is not a URL and env var '{value}' is unset or empty"
        )
    resolved = resolved.strip()
    if not resolved.startswith(("http://", "https://")):
        raise ValueError(
            f"Env var '{value}' must contain an http:// or https:// URL, "
            f"got: {resolved!r}"
        )
    return resolved


# LiteLLM appends the route itself; a base URL that already carries it sends
# requests to ``.../chat/completions/chat/completions`` → 404 (issue #717).
_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"


def normalize_base_url(url: str | None) -> str | None:
    """Return ``url`` without a redundant trailing ``/chat/completions``.

    LiteLLM's OpenAI-compatible providers append ``/chat/completions`` to
    ``base_url``; a value that already includes it produces a doubled path and a
    ``404`` on the first request (issue #717).
    """
    if not url:
        return url
    trimmed = url.rstrip("/")
    if trimmed.endswith(_CHAT_COMPLETIONS_SUFFIX):
        trimmed = trimmed[: -len(_CHAT_COMPLETIONS_SUFFIX)]
    return trimmed or url


def _strip_message_name(messages):
    """Return ``messages`` with the message-level ``name`` field dropped.

    Only tool-result messages carry ``name`` (the SDK sets it alongside
    ``tool_call_id``); other messages are passed through untouched. Pure: the
    input is never mutated.
    """
    return [
        {k: v for k, v in message.items() if k != "name"}
        if isinstance(message, dict) and message.get("role") == "tool"
        else message
        for message in (messages or [])
    ]


class _MessageNameStripper(CustomLogger):
    """LiteLLM pre-call hook dropping the tool-message ``name`` field.

    ``drop_params`` / ``additional_drop_params`` operate on top-level request
    params, never on fields nested inside ``messages``; an OpenAI-compatible
    gateway that rejects the message-level ``name`` therefore needs this
    dedicated seam. ``log_pre_api_call`` fires synchronously before every
    request (streamed included), and its ``complete_input_dict`` is the exact
    JSON body LiteLLM is about to send, so rewriting ``messages`` there is
    template-agnostic (issue #881).
    """

    def log_pre_api_call(self, model, messages, kwargs):
        complete_input = (kwargs.get("additional_args") or {}).get(
            "complete_input_dict"
        )
        if isinstance(complete_input, dict) and complete_input.get("messages") is not None:
            complete_input["messages"] = _strip_message_name(complete_input["messages"])


_MESSAGE_NAME_STRIPPER: _MessageNameStripper | None = None


def _enable_message_name_drop() -> None:
    """Install the message-``name`` stripper when a provider opted in.

    Idempotent: the process-wide ``litellm.callbacks`` list must not accumulate
    duplicate registrations across runs.
    """
    global _MESSAGE_NAME_STRIPPER
    if _MESSAGE_NAME_STRIPPER is None:
        _MESSAGE_NAME_STRIPPER = _MessageNameStripper()
    if _MESSAGE_NAME_STRIPPER not in litellm.callbacks:
        litellm.callbacks.append(_MESSAGE_NAME_STRIPPER)


def resolve_llm_config(args, prompt: str) -> tuple[dict, str, object | None]:
    """
    Resolve LLM configuration from layered sources.

    Priority order (highest → lowest):
      1. Per-invocation overrides (CONFIG_MODEL, CONFIG_TEMPERATURE, etc.)
      2. Layered config file (agent-config.yaml)
      3. Environment variables (LLM_API_KEY, LLM_MODEL, etc.)

    Args:
        args: Parsed argparse namespace with prompt_location attribute.
        prompt: The loaded prompt string (passed through unchanged).

    Returns:
        Tuple of (llm_config_dict, prompt, agent_cfg_or_none).

    Raises:
        ValueError: If no API key can be resolved.
    """
    import default_paths

    config_path = default_paths.agent_config_file()
    role_name = os.environ.get("ROLE_NAME", "unknown")

    api_key = None
    model = None
    base_url = None
    temperature = None
    agent_cfg = None
    api_url_raw = None
    tier_override = None
    effective_ref = None
    provider = None

    config_complexity = os.getenv("CONFIG_COMPLEXITY", "").lower()

    # ── Layered config file ──────────────────────────────────────
    if os.path.isfile(config_path):
        try:
            from config import load_config

            cfg = load_config(config_path)
            agent_cfg = cfg.agents.get(role_name)
            api_url_raw = None
            if agent_cfg:
                if config_complexity in ("low", "medium", "high"):
                    tier_override = agent_cfg.complexity_overrides.get(config_complexity)
                effective_ref = (
                    tier_override.model_ref
                    if tier_override and tier_override.model_ref
                    else agent_cfg.model_ref
                )
                model = effective_ref.model_path
                provider = effective_ref.provider_ref
                api_key = os.environ.get(provider.api_token_env)
                api_url_raw = provider.api_url
                temperature = agent_cfg.temperature
                if tier_override is not None and tier_override.model_ref is not None:
                    logger.info(
                        f"Complexity tier override: {config_complexity} -> {model}"
                    )
            else:
                logger.warning(
                    f"Agent role '{role_name}' not found in config; "
                    "falling back to env vars"
                )
        except Exception as e:
            logger.warning(
                f"Failed to load config from {config_path}: {e}; "
                "falling back to env vars"
            )

        # Resolve api_url outside the load try/except: a bad env-var reference
        # is a config error and must fail fast, not silently fall back.
        if api_url_raw is not None:
            base_url = resolve_api_url(api_url_raw)
            logger.info(
                f"Loaded config for agent '{role_name}' from {config_path}: "
                f"model={model}, provider={base_url}"
            )

    # ── Backward-compatible env var fallback ─────────────────────
    if api_key is None:
        api_key = os.getenv("LLM_API_KEY")
    if model is None:
        # No silent default: an unresolved model must fail loudly below rather
        # than reach the provider as a bogus name and 404 (issue #717).
        model = os.getenv("LLM_MODEL")
    if base_url is None:
        base_url = os.getenv("LLM_BASE_URL")
    if temperature is None and os.getenv("LLM_TEMPERATURE"):
        temperature = float(os.getenv("LLM_TEMPERATURE"))

    # ── Per-invocation overrides ─────────────────────────────────
    config_model = os.getenv("CONFIG_MODEL")
    if config_model:
        model = config_model
        logger.info(f"CONFIG_MODEL override: {model}")

    config_temperature = os.getenv("CONFIG_TEMPERATURE")
    if config_temperature:
        temperature = float(config_temperature)
        logger.info(f"CONFIG_TEMPERATURE override: {temperature}")
    else:
        complexity_temperature_map = {"low": 0.0, "medium": 0.1, "high": 0.3}
        if config_complexity in complexity_temperature_map:
            if tier_override is not None and tier_override.temperature is not None:
                temperature = tier_override.temperature
            complexity_temp = complexity_temperature_map[config_complexity]
            if temperature is not None:
                temperature = max(temperature, complexity_temp)
            else:
                temperature = complexity_temp
            logger.info(
                f"Complexity-based temperature: complexity={config_complexity} -> {temperature}"
            )

    # ── Validation ───────────────────────────────────────────────
    if not api_key:
        raise ValueError("LLM_API_KEY environment variable is not set.")
    if not model:
        raise ValueError(
            f"No LLM model resolved: agent role '{role_name}' has no entry in "
            f"'{config_path}' and LLM_MODEL is unset."
        )

    # Normalize last: every source (config, env fallback, override) has been
    # folded into ``base_url`` by now, so the suffix strip covers them all.
    base_url = normalize_base_url(base_url)

    # ── Build base LLM config dict ───────────────────────────────
    llm_config = {
        "model": model,
        "api_key": api_key,
        "usage_id": "agent_script",
        "drop_params": True,
        "timeout": LLM_TIMEOUT_S,
        # Streaming keeps the connection warm across a long reasoning phase:
        # the SDK only keeps it on when an ``on_token`` sink is wired, which
        # run_agent does (issue #775).
        "stream": True,
    }

    if base_url:
        llm_config["base_url"] = base_url

    if temperature is not None:
        llm_config["temperature"] = temperature

    # ── Brand attribution (OpenRouter HTTP-Referer / X-Title) ────
    # The SDK defaults these to OpenHands; override them from the single
    # BrandIdentity source of truth so requests appear as Nebuko.
    from config import brand

    _brand = brand()
    llm_config["openrouter_app_name"] = _brand.app_name
    llm_config["openrouter_site_url"] = str(_brand.site_url)

    # ── Per-token pricing ────────────────────────────────────────
    # Normalize the per-million operator prices to per-token costs and flow
    # them into LLM(**llm_config) so the SDK can compute session cost (LiteLLM
    # custom_cost_per_token). CostRates yields nothing unless *both* prices are
    # set — the SDK ignores custom pricing when only one key is present — and
    # keys are omitted entirely when unset so LiteLLM's built-in model-map
    # pricing still applies.
    if effective_ref is not None:
        rates = CostRates.from_prices(
            effective_ref.input_price, effective_ref.output_price
        )
        if rates is not None:
            llm_config["input_cost_per_token"] = rates.input_per_token
            llm_config["output_cost_per_token"] = rates.output_per_token
            logger.info(
                f"Per-token pricing set from config prices "
                f"(input={effective_ref.input_price}, "
                f"output={effective_ref.output_price} per M tokens)"
            )

    # ── Thinking parameters (litellm_extra_body) ─────────────────
    # Qwen3.x flexible thinking rides chat_template_kwargs, not the
    # top-level OpenAI-style reasoning_effort field.
    litellm_extra_body = {}
    chat_template_kwargs = {}

    if effective_ref and effective_ref.thinking_enabled is not None:
        chat_template_kwargs["enable_thinking"] = effective_ref.thinking_enabled
    if effective_ref and effective_ref.preserve_thinking is not None:
        chat_template_kwargs["preserve_thinking"] = effective_ref.preserve_thinking

    # Reasoning effort: ``none``/unset omits the param so the model default
    # (xhigh) applies; only emitted while thinking is on.
    llm_reasoning = os.getenv("LLM_REASONING", "none")
    if llm_reasoning in ("", "none") and tier_override and tier_override.reasoning:
        llm_reasoning = tier_override.reasoning
    reasoning_effort = normalize_reasoning_effort(llm_reasoning)
    thinking_on = chat_template_kwargs.get("enable_thinking", True)
    if reasoning_effort and thinking_on:
        chat_template_kwargs["reasoning_effort"] = reasoning_effort
        logger.info(f"Reasoning effort: {reasoning_effort}")

    if chat_template_kwargs:
        litellm_extra_body["chat_template_kwargs"] = chat_template_kwargs

    if effective_ref and effective_ref.thinking_token_budget is not None:
        litellm_extra_body["thinking_token_budget"] = effective_ref.thinking_token_budget

    # ── Sampling parameter overrides ─────────────────────────────
    # Priority: per-invocation override > complexity tier override
    #           > per-agent override > per-LLM config
    sampling_params = [
        ("top_p", os.getenv("CONFIG_TOP_P")),
        ("top_k", os.getenv("CONFIG_TOP_K")),
        ("min_p", os.getenv("CONFIG_MIN_P")),
        ("presence_penalty", os.getenv("CONFIG_PRESENCE_PENALTY")),
        ("repetition_penalty", os.getenv("CONFIG_REPETITION_PENALTY")),
    ]
    for param_name, config_val in sampling_params:
        if config_val is not None and str(config_val).strip():
            litellm_extra_body[param_name] = float(config_val)
        elif tier_override is not None and getattr(tier_override, param_name, None) is not None:
            litellm_extra_body[param_name] = float(getattr(tier_override, param_name))
        elif agent_cfg:
            agent_val = getattr(agent_cfg, param_name, None)
            llm_val = getattr(effective_ref, param_name, None) if effective_ref else None
            val = agent_val if agent_val is not None else llm_val
            if val is not None:
                litellm_extra_body[param_name] = val

    # ── Stop sequences ───────────────────────────────────────────
    if agent_cfg:
        agent_stop = getattr(agent_cfg, "stop_sequences", None)
        llm_stop = getattr(effective_ref, "stop_sequences", None) if effective_ref else None
        stop_seq = agent_stop if agent_stop else llm_stop
        if stop_seq:
            litellm_extra_body["stop"] = stop_seq

    if litellm_extra_body:
        llm_config["litellm_extra_body"] = litellm_extra_body
        logger.info(f"Thinking parameters: {litellm_extra_body}")

    # ── Max output tokens ────────────────────────────────────────
    if effective_ref and effective_ref.max_output_tokens is not None:
        llm_config["max_output_tokens"] = effective_ref.max_output_tokens

    # ── Provider HTTP decoration (client identity + issue session) ──
    # The engine stamps ``LLM_SESSION_ID`` from the trigger's issue number; the
    # provider's declared header *name* decides whether it is sent. Config never
    # carries the value. Factory order is significant: the session runtime is
    # last, so the engine value wins on a key collision.
    if provider is not None:
        headers: dict[str, str] = {}
        for runtime in build_provider_runtime(provider, os.environ):
            headers.update(runtime.extra_headers())
        if headers:
            llm_config["extra_headers"] = headers

        # Provider opted out of the message-level ``name`` field (issue #881):
        # gate the pre-call stripper on the provider, never globally, so
        # providers that rely on ``name`` keep it.
        if provider.drop_message_name:
            _enable_message_name_drop()

    return (llm_config, prompt, agent_cfg)
