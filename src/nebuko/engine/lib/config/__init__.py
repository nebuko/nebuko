"""Configuration management package."""

# App Config sub-domains
from .sub_domains.brand import BRAND, BrandIdentity, brand
from .sub_domains.provider_config.provider_config import ProviderConfig
from .sub_domains.llm_config.llm_config import LLMConfig
from .sub_domains.agent_complexity_override.agent_complexity_override import AgentComplexityOverride
from .sub_domains.agent_config.agent_config import AgentConfig
from .sub_domains.agent_start_notice.agent_start_notice import AgentStartNotice
from .sub_domains.app_config.app_config import AppConfig

# Workflow Config sub-domains
from .sub_domains.workflow_condition.workflow_condition import WorkflowCondition
from .sub_domains.workflow_step.script_step_spec import ScriptStepSpec
from .sub_domains.workflow_step.workflow_step import WorkflowStep
from .sub_domains.workflow_trigger.workflow_trigger import WorkflowTrigger
from .sub_domains.workflow_definition.workflow_definition import WorkflowDefinition
from .sub_domains.workflow_position.workflow_position import (
    POSITION_STATE_VERSION,
    WorkflowPosition,
    is_self_function_call,
    parse_position_env,
)
from .sub_domains.step_ref.step_ref import StepRef

# Features
from .features.resolve_refs.resolve_refs import resolve_refs, REF_PATTERN
from .features.load_config.load_config import load_config
from .features.load_agent_names.load_agent_names import (
    DEFAULT_AGENT_NAMES,
    load_agent_names,
)
from .features.load_workflow.load_workflow import (
    is_path_like_reference,
    is_valid_workflow_reference,
    load_workflow,
    load_workflow_with_dirs,
    resolve_workflow_reference,
)

# Facade module constants
from .config import CONFIG_DEFAULT_PATH

__all__ = [
    # App Config entities
    "BrandIdentity",
    "brand",
    "BRAND",
    "ProviderConfig",
    "LLMConfig",
    "AgentComplexityOverride",
    "AgentConfig",
    "AgentStartNotice",
    "AppConfig",
    # Workflow Config entities
    "WorkflowCondition",
    "ScriptStepSpec",
    "WorkflowStep",
    "WorkflowTrigger",
    "WorkflowDefinition",
    "WorkflowPosition",
    "is_self_function_call",
    "POSITION_STATE_VERSION",
    "parse_position_env",
    "StepRef",
    # Features
    "resolve_refs",
    "REF_PATTERN",
    "load_config",
    "load_agent_names",
    "DEFAULT_AGENT_NAMES",
    "load_workflow",
    "load_workflow_with_dirs",
    "is_path_like_reference",
    "is_valid_workflow_reference",
    "resolve_workflow_reference",
    # Constants
    "CONFIG_DEFAULT_PATH",
]
