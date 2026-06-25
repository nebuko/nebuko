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
from pydantic import BaseModel, Field, model_validator


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
    """Model path, provider reference, optional pricing, reasoning, and sampling parameters."""

    model_path: str
    provider_ref: ProviderConfig = Field(alias="provider")
    default_temperature: float = 0.0
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    # Reasoning / thinking parameters (Qwen3, vLLM, etc.)
    thinking_enabled: Optional[bool] = None
    preserve_thinking: Optional[bool] = None
    thinking_token_budget: Optional[int] = None
    max_output_tokens: Optional[int] = None
    # Sampling parameters for instruction-following control
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    min_p: Optional[float] = None
    presence_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    stop_sequences: Optional[list[str]] = None


class AgentConfig(BaseModel):
    """Agent role → model + prompt file + skills + tools."""

    model_ref: LLMConfig = Field(alias="model")
    temperature: float = 0.0
    prompt_file: str
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(
        default=[
            "terminal", "file_editor", "task_tracker", "think", "finish",
        ]
    )
    # Per-agent sampling parameter overrides
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    min_p: Optional[float] = None
    presence_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    stop_sequences: Optional[list[str]] = None


class WorkflowCondition(BaseModel):
    """Conditional routing within a workflow step."""

    on_result: str
    next: Optional[str] = None


class WorkflowStep(BaseModel):
    """A single step in a workflow: agent + routing.

    A step can be:
    - An agent invocation (agent)
    - A local sub-workflow reference (workflow, optionally with path)
    - A remote git repo workflow (repo + ref + optional workflow_name)
    """

    name: str
    agent: Optional[str] = None
    workflow: Optional[str] = None
    path: Optional[str] = None
    # Git-based remote workflow source
    repo: Optional[str] = None
    ref: Optional[str] = None
    workflow_name: Optional[str] = None
    next: Optional[str] = None
    conditions: Optional[list[WorkflowCondition]] = None
    skill: Optional[list[str]] = None

    @model_validator(mode="after")
    def _validate_step_source(self) -> "WorkflowStep":
        # Note: `path` is an optional companion to `workflow`, not a standalone source.
        # `repo` is mutually exclusive with `agent`, `workflow`, and `path`.
        if not self.agent and not self.workflow and not self.repo:
            raise ValueError(
                "Step must have exactly one of 'agent', 'workflow', or 'repo'"
            )
        if self.agent and self.workflow:
            raise ValueError(
                "Step cannot have both 'agent' and 'workflow'"
            )
        if self.agent and self.repo:
            raise ValueError(
                "Step cannot have both 'agent' and 'repo'"
            )
        if self.workflow and self.repo:
            raise ValueError(
                "Step cannot have both 'workflow' and 'repo'"
            )
        if self.repo and self.path:
            raise ValueError(
                "Step cannot have both 'repo' and 'path'"
            )

        if self.repo:
            if not self.ref:
                raise ValueError("'repo' requires 'ref' (commit SHA, tag, or branch)")
            if not self.repo.startswith("https://"):
                raise ValueError("'repo' URL must use HTTPS scheme")
            if self.workflow_name:
                if ".." in self.workflow_name or self.workflow_name.startswith("/"):
                    raise ValueError(
                        "'workflow_name' must not contain '..' or start with '/'"
                    )
        return self


class WorkflowTrigger(BaseModel):
    """Which @@mentions can start this workflow."""

    agents: list[str]


class WorkflowDefinition(BaseModel):
    """A complete workflow definition."""

    name: str
    description: str
    trigger: WorkflowTrigger
    steps: list[WorkflowStep]


class AppConfig(BaseModel):
    """Top-level application config: providers → llms → agents."""

    providers: dict[str, ProviderConfig]
    llms: dict[str, LLMConfig]
    agents: dict[str, AgentConfig]


# ── Public API ───────────────────────────────────────────────────────


def load_workflow(path: str) -> WorkflowDefinition:
    """Load and validate a workflow definition YAML file.

    Args:
        path: Path to the workflow YAML file.

    Returns:
        A validated WorkflowDefinition instance.

    Raises:
        FileNotFoundError: If the workflow file does not exist.
        ValueError: If the config fails validation or cannot be parsed.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Workflow file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(
            f"Workflow file must contain a YAML mapping, got {type(raw).__name__}"
        )

    return WorkflowDefinition.model_validate(raw)


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
