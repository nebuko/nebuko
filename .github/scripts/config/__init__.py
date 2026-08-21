"""Configuration management package."""

# App Config sub-domains
from .sub_domains.provider_config.provider_config import ProviderConfig
from .sub_domains.llm_config.llm_config import LLMConfig
from .sub_domains.agent_config.agent_config import AgentConfig
from .sub_domains.app_config.app_config import AppConfig

# Workflow Config sub-domains
from .sub_domains.workflow_condition.workflow_condition import WorkflowCondition
from .sub_domains.workflow_step.workflow_step import WorkflowStep
from .sub_domains.workflow_trigger.workflow_trigger import WorkflowTrigger
from .sub_domains.workflow_definition.workflow_definition import WorkflowDefinition

# Features
from .features.resolve_refs.resolve_refs import resolve_refs, REF_PATTERN
from .features.load_config.load_config import load_config
from .features.load_workflow.load_workflow import (
    is_path_like_reference,
    is_valid_workflow_reference,
    load_workflow,
    resolve_workflow_reference,
)
from .features.roster.roster import (
    KNOWN_TOOLS,
    STATIC_TASK_TO_PROMPT,
    build_agent_roster,
    validate_roster,
    build_mention_regex,
    extract_caller_agent,
    load_agent_roster,
)

# Facade module constants
from .config import CONFIG_DEFAULT_PATH

__all__ = [
    # App Config entities
    "ProviderConfig",
    "LLMConfig",
    "AgentConfig",
    "AppConfig",
    # Workflow Config entities
    "WorkflowCondition",
    "WorkflowStep",
    "WorkflowTrigger",
    "WorkflowDefinition",
    # Features
    "resolve_refs",
    "REF_PATTERN",
    "load_config",
    "load_workflow",
    "is_path_like_reference",
    "is_valid_workflow_reference",
    "resolve_workflow_reference",
    # Roster feature
    "KNOWN_TOOLS",
    "STATIC_TASK_TO_PROMPT",
    "build_agent_roster",
    "validate_roster",
    "build_mention_regex",
    "extract_caller_agent",
    "load_agent_roster",
    # Constants
    "CONFIG_DEFAULT_PATH",
]
