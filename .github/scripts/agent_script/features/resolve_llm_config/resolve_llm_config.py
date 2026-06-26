"""Resolve LLM config feature — config file parsing, env fallback, and override handling."""

import os

from openhands.sdk import get_logger

logger = get_logger(__name__)


def resolve_llm_config(args, prompt: str) -> tuple[dict, str]:
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
        Tuple of (llm_config_dict, prompt).

    Raises:
        ValueError: If no API key can be resolved.
    """
    config_path = os.getenv("AGENT_CONFIG_PATH", "agent-config.yaml")
    role_name = os.environ.get("ROLE_NAME", "unknown")

    api_key = None
    model = None
    base_url = None
    temperature = None
    agent_cfg = None

    # ── Layered config file ──────────────────────────────────────
    if os.path.isfile(config_path):
        try:
            from config import load_config

            cfg = load_config(config_path)
            agent_cfg = cfg.agents.get(role_name)
            if agent_cfg:
                model = agent_cfg.model_ref.model_path
                provider = agent_cfg.model_ref.provider_ref
                api_key = os.environ.get(provider.api_token_env)
                base_url = provider.api_url
                temperature = agent_cfg.temperature
                logger.info(
                    f"Loaded config for agent '{role_name}' from {config_path}: "
                    f"model={model}, provider={provider.api_url}"
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

    # ── Backward-compatible env var fallback ─────────────────────
    if api_key is None:
        api_key = os.getenv("LLM_API_KEY")
    if model is None:
        model = os.getenv("LLM_MODEL", "gpt-5.5")
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
        config_complexity = os.getenv("CONFIG_COMPLEXITY", "").lower()
        complexity_temperature_map = {"low": 0.0, "medium": 0.1, "high": 0.3}
        if config_complexity in complexity_temperature_map:
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

    # ── Build base LLM config dict ───────────────────────────────
    llm_config = {
        "model": model,
        "api_key": api_key,
        "usage_id": "agent_script",
        "drop_params": True,
    }

    if base_url:
        llm_config["base_url"] = base_url

    if temperature is not None:
        llm_config["temperature"] = temperature

    # ── Reasoning effort ─────────────────────────────────────────
    llm_reasoning = os.getenv("LLM_REASONING", "none")
    if llm_reasoning and llm_reasoning != "none":
        llm_config["reasoning_effort"] = llm_reasoning
        logger.info(f"Reasoning enabled: effort={llm_reasoning}")

    # ── Thinking parameters (litellm_extra_body) ─────────────────
    litellm_extra_body = {}
    chat_template_kwargs = {}

    if agent_cfg and agent_cfg.model_ref.thinking_enabled is not None:
        chat_template_kwargs["enable_thinking"] = agent_cfg.model_ref.thinking_enabled
    if agent_cfg and agent_cfg.model_ref.preserve_thinking is not None:
        chat_template_kwargs["preserve_thinking"] = agent_cfg.model_ref.preserve_thinking

    if chat_template_kwargs:
        litellm_extra_body["chat_template_kwargs"] = chat_template_kwargs

    if agent_cfg and agent_cfg.model_ref.thinking_token_budget is not None:
        litellm_extra_body["thinking_token_budget"] = agent_cfg.model_ref.thinking_token_budget

    # ── Sampling parameter overrides ─────────────────────────────
    # Priority: per-invocation override > per-agent override > per-LLM config
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
        elif agent_cfg:
            agent_val = getattr(agent_cfg, param_name, None)
            llm_val = getattr(agent_cfg.model_ref, param_name, None)
            val = agent_val if agent_val is not None else llm_val
            if val is not None:
                litellm_extra_body[param_name] = val

    # ── Stop sequences ───────────────────────────────────────────
    if agent_cfg:
        agent_stop = getattr(agent_cfg, "stop_sequences", None)
        llm_stop = getattr(agent_cfg.model_ref, "stop_sequences", None)
        stop_seq = agent_stop if agent_stop else llm_stop
        if stop_seq:
            litellm_extra_body["stop"] = stop_seq

    if litellm_extra_body:
        llm_config["litellm_extra_body"] = litellm_extra_body
        logger.info(f"Thinking parameters: {litellm_extra_body}")

    # ── Max output tokens ────────────────────────────────────────
    if agent_cfg and agent_cfg.model_ref.max_output_tokens is not None:
        llm_config["max_output_tokens"] = agent_cfg.model_ref.max_output_tokens

    return (llm_config, prompt)
