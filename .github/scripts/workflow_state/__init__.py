"""Workflow state management package.

Package-by-feature DDD layout:
  sub_domains/     — domain entities (HistoryEntry, Dispatch, IssueInfo, ...)
  features/        — state-transition / I/O features (build_state, to_json, ...)
  workflow_state.py — facade (WorkflowState model + convenience functions)
"""

# Domain entities
from .sub_domains.history_entry.history_entry import HistoryEntry
from .sub_domains.dispatch.dispatch import Dispatch
from .sub_domains.issue_info.issue_info import IssueInfo
from .sub_domains.pull_request_info.pull_request_info import PullRequestInfo
from .sub_domains.state_metrics.state_metrics import StateMetrics
from .sub_domains.step_resolution.step_resolution import StepResolution
from .sub_domains.function_call import FunctionCall, SUPPORTED_FUNCTIONS
from .sub_domains.parent_ref import ParentRef

# Features
from .features.build_state.build_state import build_state
from .features.to_json.to_json import state_to_json
from .features.comment.to_git_comment import format_collapsed_section
from .features.get_last_state.get_last_state import (
    extract_latest_state_from_comments,
    extract_state_from_body,
)

# Dispatch feature
from .sub_domains.dispatch.features.get_dispatch_from_state.get_dispatch_from_state import (
    extract_dispatch_from_state,
)

# FunctionCall feature
from .sub_domains.function_call.features.extract_function_call.extract_function_call import (
    extract_function_call,
)

# Facade — import from the module directly to avoid circular import with this package
from .workflow_state import (
    WorkflowState,
    STATE_MARKER_START,
    STATE_MARKER_END,
    to_json,
    to_git_comment,
    from_body,
    go_next,
)

__all__ = [
    # Domain entities
    "HistoryEntry",
    "Dispatch",
    "IssueInfo",
    "PullRequestInfo",
    "StateMetrics",
    "StepResolution",
    "FunctionCall",
    "SUPPORTED_FUNCTIONS",
    "ParentRef",
    # Features
    "build_state",
    "state_to_json",
    "format_collapsed_section",
    "extract_latest_state_from_comments",
    "extract_state_from_body",
    "extract_dispatch_from_state",
    "extract_function_call",
    # Facade
    "WorkflowState",
    "STATE_MARKER_START",
    "STATE_MARKER_END",
    "to_json",
    "to_git_comment",
    "from_body",
    "go_next",
]
