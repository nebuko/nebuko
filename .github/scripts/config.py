"""
Layered configuration system for the multi-agent workflow.

Provides Pydantic-validated config models and a ${ref} resolver for
cross-references between providers, LLMs, and agents.

Usage:
    from config import load_config

    config = load_config("agent-config.yaml")
    agent_cfg = config.agents["dev"]
    provider = agent_cfg.model_ref.provider_ref
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field


# ── ${ref} Resolver ──────────────────────────────────────────────────

REF_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_.\-]*)\}")


def resolve_refs(config: dict | list | str, root: dict | None = None) -> Any:
    """Resolve ``${providers.x}`` and ``${llms.y}`` references recursively.

    * Full-string match (e.g. ``"${providers.openrouter}"``) → returns the
      referenced object (dict) directly.
    * Partial inline substitution (e.g. ``"prefix_${llms.x.model_path}_suffix"``)
      → substitutes string values only.
    """
    if root is None:
        root = config

    if isinstance(config, dict):
        return {k: resolve_refs(v, root) for k, v in config.items()}

    if isinstance(config, list):
        return [resolve_refs(item, root) for item in config]

    if isinstance(config, str):
        # Full-string match → return the resolved object (recursively)
        if REF_PATTERN.fullmatch(config):
            parts = REF_PATTERN.fullmatch(config).group(1).split(".")
            obj = root
            for part in parts:
                obj = obj[part]
            return resolve_refs(obj, root)

        # Partial inline substitution
        def _replacer(match: re.Match) -> str:
            parts = match.group(1).split(".")
            obj = root
            for part in parts:
                obj = obj[part]
            return str(obj) if not isinstance(obj, (dict, list)) else match.group(0)

        return REF_PATTERN.sub(_replacer, config)

    return config


# ── Pydantic Models ──────────────────────────────────────────────────


class ProviderConfig(BaseModel):
    """API endpoint + auth token environment variable name."""

    api_url: str
    api_token_env: str


class LLMConfig(BaseModel):
    """Model path, provider reference, and optional pricing."""

    model_path: str
    provider_ref: ProviderConfig = Field(alias="provider")
    default_temperature: float = 0.0
    input_price: Optional[float] = None
    output_price: Optional[float] = None


class AgentConfig(BaseModel):
    """Agent role → model + prompt file + skills."""

    model_ref: LLMConfig = Field(alias="model")
    temperature: float = 0.0
    prompt_file: str
    skills: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    """Top-level application config: providers → llms → agents."""

    providers: dict[str, ProviderConfig]
    llms: dict[str, LLMConfig]
    agents: dict[str, AgentConfig]


# ── Public API ───────────────────────────────────────────────────────


def load_config(path: str = "agent-config.yaml") -> AppConfig:
    """Load, resolve, and validate a layered config file.

    Args:
        path: Path to the YAML config file.

    Returns:
        A validated AppConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config fails validation or cannot be parsed.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML mapping, got {type(raw).__name__}")

    resolved = resolve_refs(raw)
    return AppConfig.model_validate(resolved)
