"""Configuration management package."""

# App Config sub-domains
from .sub_domains.brand import BRAND, BrandIdentity, brand
from .sub_domains.provider_config.provider_config import ProviderConfig
from .sub_domains.llm_config.llm_config import LLMConfig
from .sub_domains.agent_complexity_override.agent_complexity_override import AgentComplexityOverride
from .sub_domains.complexity_policy.complexity_policy import (
    BUILTIN_LAYER,
    TIERS,
    ComplexityBand,
    ComplexityPolicy,
    ComplexityPolicyLayer,
    Envelope,
    MetricBound,
    SignalRule,
    build_builtin_policy,
)
from .sub_domains.token_budget.token_budget import TokenBudgetPolicy
from .sub_domains.mcp_server_config.mcp_server_config import MCPServerConfig
from .sub_domains.agent_config.agent_config import AgentConfig
from .sub_domains.agent_start_notice.agent_start_notice import AgentStartNotice
from .sub_domains.human_action_notice.human_action_notice import HumanActionNotice
from .sub_domains.script_start_notice.script_start_notice import ScriptStartNotice
from .sub_domains.script_step_report.script_step_report import ScriptStepReport
from .sub_domains.app_config.app_config import AppConfig
from .sub_domains.history_window.history_window import HistoryWindowConfig
from .sub_domains.comment_size.comment_size import CommentSizePolicy

# Workflow Config sub-domains
from .sub_domains.workflow.input_binding import (
    REF_PATTERN as INPUT_REF_PATTERN,
    InputBinding,
    InputMaterializationError,
)
from .sub_domains.workflow.workflow_input_spec import WorkflowInputSpec
from .sub_domains.workflow_condition.workflow_condition import WorkflowCondition
from .sub_domains.workflow_step.hook_spec import HookSpec
from .sub_domains.workflow_step.script_step_spec import ScriptStepSpec
from .sub_domains.workflow_step.workflow_step import WorkflowStep
from .sub_domains.workflow_definition.workflow_definition import WorkflowDefinition
from .sub_domains.workflow_position.workflow_position import (
    POSITION_STATE_VERSION,
    WorkflowPosition,
    is_self_function_call,
    parse_position_env,
)
from .sub_domains.step_ref.step_ref import StepRef
from .sub_domains.step_path.step_path import SEGMENT_PATTERN, StepPath

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
    "ComplexityPolicy",
    "ComplexityPolicyLayer",
    "ComplexityBand",
    "SignalRule",
    "MetricBound",
    "Envelope",
    "BUILTIN_LAYER",
    "TIERS",
    "build_builtin_policy",
    "TokenBudgetPolicy",
    "MCPServerConfig",
    "AgentConfig",
    "AgentStartNotice",
    "HumanActionNotice",
    "ScriptStartNotice",
    "ScriptStepReport",
    "AppConfig",
    "HistoryWindowConfig",
    "CommentSizePolicy",
    # Workflow Config entities
    "WorkflowCondition",
    "WorkflowInputSpec",
    "InputBinding",
    "InputMaterializationError",
    "INPUT_REF_PATTERN",
    "HookSpec",
    "ScriptStepSpec",
    "WorkflowStep",
    "WorkflowDefinition",
    "WorkflowPosition",
    "is_self_function_call",
    "POSITION_STATE_VERSION",
    "parse_position_env",
    "StepRef",
    "StepPath",
    "SEGMENT_PATTERN",
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
