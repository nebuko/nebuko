"""Workflow state management package.

Package-by-feature DDD layout:
  sub_domains/     — domain entities (HistoryEntry, TurnOutcome, IssueInfo, ...)
  features/        — state-transition / I/O features (build_state, to_json, ...)
  workflow_state.py — facade (WorkflowState model + convenience functions)
"""

# Domain entities
from .sub_domains.history_entry.history_entry import HistoryEntry
from .sub_domains.issue_info.issue_info import IssueInfo
from .sub_domains.pull_request_info.pull_request_info import PullRequestInfo
from .sub_domains.state_metrics.state_metrics import StateMetrics
from .sub_domains.step_resolution.step_resolution import StepResolution
from .sub_domains.function_call import FunctionCall, SUPPORTED_FUNCTIONS
from .sub_domains.gate_decision.gate_decision import GateDecision
from .sub_domains.thread_record.thread_record import ThreadRecord, ThreadRecordKind
from .sub_domains.step_input_record.step_input_record import StepInputRecord
from .sub_domains.run_prefs.run_prefs import RunPrefs
from .sub_domains.turn_outcome.turn_outcome import (
    Appreciation,
    HoldKind,
    IncompleteReason,
    TurnOutcome,
    TurnStatus,
)

# Features
from .features.build_state.build_state import build_state
from .step_output_record import StepOutputRecord
from .features.to_json.to_json import state_to_json
from .features.comment.to_git_comment import format_collapsed_section
from .features.get_last_state.get_last_state import (
    extract_latest_state_from_comments,
    extract_state_from_body,
)

# TurnOutcome feature
from .sub_domains.turn_outcome.features.get_turn_outcome_from_state.get_turn_outcome_from_state import (
    extract_turn_outcome_from_state,
)

# FunctionCall feature
from .sub_domains.function_call.features.extract_function_call.extract_function_call import (
    extract_function_call,
)

# Facade — import from the module directly to avoid circular import with this package
from .workflow_state import (
    WorkflowState,
    SoftRetryState,
    CircuitBreakerState,
    STATE_SCHEMA_VERSION,
    STATE_MARKER_START,
    STATE_MARKER_END,
    to_json,
    to_git_comment,
    from_body,
    go_next,
    workflow_ref_from_env,
)

__all__ = [
    # Domain entities
    "HistoryEntry",
    "RunPrefs",
    "IssueInfo",
    "PullRequestInfo",
    "StateMetrics",
    "StepResolution",
    "FunctionCall",
    "SUPPORTED_FUNCTIONS",
    "GateDecision",
    "ThreadRecord",
    "ThreadRecordKind",
    "StepInputRecord",
    "Appreciation",
    "HoldKind",
    "IncompleteReason",
    "TurnOutcome",
    "TurnStatus",
    # Features
    "build_state",
    "state_to_json",
    "format_collapsed_section",
    "extract_latest_state_from_comments",
    "extract_state_from_body",
    "extract_turn_outcome_from_state",
    "extract_function_call",
    # Facade
    "WorkflowState",
    "SoftRetryState",
    "CircuitBreakerState",
    "STATE_SCHEMA_VERSION",
    "STATE_MARKER_START",
    "STATE_MARKER_END",
    "to_json",
    "to_git_comment",
    "from_body",
    "go_next",
    "workflow_ref_from_env",
]
