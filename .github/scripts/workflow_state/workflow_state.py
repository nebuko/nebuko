from pydantic import BaseModel, Field
from typing import Any, Optional

# Import domain entities first (no circular dependency)
from .sub_domains.history_entry.history_entry import HistoryEntry
from .sub_domains.state_metrics.state_metrics import StateMetrics
from .sub_domains.issue_info.issue_info import IssueInfo
from .sub_domains.pull_request_info.pull_request_info import PullRequestInfo
from .sub_domains.dispatch.dispatch import Dispatch
from .sub_domains.step_resolution.step_resolution import StepResolution

STATE_MARKER_START = "<!-- WORKFLOW_STATE_START -->"
STATE_MARKER_END = "<!-- WORKFLOW_STATE_END -->"

class WorkflowState(BaseModel):
    """
    Structured workflow state embedded in GitHub comments.

    Provides typed, machine-parseable state management replacing the previous
    HTML-comment-based phase detection. Every agent step produces a fresh
    full-snapshot state (Strategy 1 + summarization).

    Fields:
        version: Monotonically increasing version number for each state update.
        workflow: Name of the active workflow (e.g., "spec-driven-design").
        step: Current step name within the workflow.
        agent: Agent role executing the current step.
        phase: High-level phase (e.g., "analysis", "planning", "execution").
        history: Summarized records of all prior steps in this workflow run.
        pinned_comment_ids: GitHub comment IDs whose full content should be
            fetched at runtime for high-fidelity context.
        metrics: Metrics from the most recent agent invocation.
        current_context: Free-form context dict for shared data between agents
            (pending tasks, constraints, decisions, etc.).
        parent_workflow: Parent workflow name if currently in a sub-workflow.
        parent_workflow_step: Step in parent that triggered the sub-workflow.
        issue: Static issue metadata (number, URL, title, author, repo).
        pull_request: Static PR metadata (number, URL, title, author, repo).
    """

    version: int = 1
    workflow: str = ""
    step: str = ""
    agent: str = ""
    phase: str = ""

    history: list[HistoryEntry] = Field(default_factory=list)
    pinned_comment_ids: list[int] = Field(default_factory=list)
    metrics: Optional[StateMetrics] = None
    current_context: dict[str, Any] = Field(default_factory=dict)
    parent_workflow: Optional[str] = None
    parent_workflow_step: Optional[str] = None
    issue: Optional[IssueInfo] = None
    pull_request: Optional[PullRequestInfo] = None
    dispatch: Optional[Dispatch] = None
    resolution: Optional[StepResolution] = None

def to_json(state: WorkflowState) -> str:
    from .features.to_json.to_json import state_to_json
    return state_to_json(state)

def to_git_comment(state: WorkflowState) -> str:
    from .features.comment.to_git_comment import format_collapsed_section
    return format_collapsed_section(state)

def from_body(body: str) -> Optional[WorkflowState]:
    from .features.get_last_state.get_last_state import extract_state_from_body
    return extract_state_from_body(body)

def go_next(
    prev_state: Optional[WorkflowState] = None,
    *,
    workflow: str = "",
    step: str = "",
    agent: str = "",
    phase: str = "",
    summary: str = "",
    comment_id: Optional[int] = None,
    artifacts: Optional[list[str]] = None,
    pin: bool = False,
    result: Optional[str] = None,
    metrics_dict: Optional[dict[str, Any]] = None,
    context_updates: Optional[dict[str, Any]] = None,
    parent_workflow: Optional[str] = None,
    parent_workflow_step: Optional[str] = None,
    issue: Optional[IssueInfo] = None,
    pull_request: Optional[PullRequestInfo] = None,
    resolution: Optional[StepResolution] = None,
    dispatch: Optional[Dispatch] = None,
) -> WorkflowState:
    from .features.build_state.build_state import build_state
    return build_state(
        prev_state=prev_state,
        workflow=workflow,
        step=step,
        agent=agent,
        phase=phase,
        summary=summary,
        comment_id=comment_id,
        artifacts=artifacts,
        pin=pin,
        result=result,
        metrics_dict=metrics_dict,
        context_updates=context_updates,
        parent_workflow=parent_workflow,
        parent_workflow_step=parent_workflow_step,
        issue=issue,
        pull_request=pull_request,
        resolution=resolution,
        dispatch=dispatch,
    )
