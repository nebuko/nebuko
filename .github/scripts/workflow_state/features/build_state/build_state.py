from typing import Optional, Any

from ...workflow_state import WorkflowState
from ...sub_domains.issue_info.issue_info import IssueInfo
from ...sub_domains.pull_request_info.pull_request_info import PullRequestInfo
from ...sub_domains.history_entry.history_entry import HistoryEntry
from ...sub_domains.state_metrics.state_metrics import StateMetrics

def build_state(
    prev_state: Optional[WorkflowState],
    *,
    workflow: str = "",
    step: str = "",
    agent: str = "",
    phase: str = "",
    summary: str = "",
    comment_id: Optional[int] = None,
    commit_id: Optional[str] = None,
    artifacts: Optional[list[str]] = None,
    pin: bool = False,
    metrics_dict: Optional[dict[str, Any]] = None,
    context_updates: Optional[dict[str, Any]] = None,
    parent_workflow: Optional[str] = None,
    parent_workflow_step: Optional[str] = None,
    issue: Optional[IssueInfo] = None,
    pull_request: Optional[PullRequestInfo] = None,
) -> WorkflowState:
    """
    Build a new WorkflowState by advancing from a previous state.

    This is a pure state-transition function: given a previous state (or None
    for initial state) and the current step's data, returns a new WorkflowState
    with the history advanced and all fields updated.

    Args:
        prev_state: Previous state, or None for initial state.
        workflow: Current workflow name.
        step: Current step name.
        agent: Current agent role.
        phase: Current phase name.
        summary: 1-2 sentence summary of this step's output.
        comment_id: GitHub comment ID for this step's output.
        artifacts: List of artifact names produced by this step.
        pin: If True, add this step's comment_id to pinned_comment_ids.
        metrics_dict: Full metrics dict from the current agent run.
        context_updates: Key-value pairs to merge into current_context.
        parent_workflow: Parent workflow name (if in a sub-workflow).
        parent_workflow_step: Parent step name (if in a sub-workflow).
        issue: Issue metadata (once set, persists across transitions).
        pull_request: PR metadata (once set, persists across transitions).

    Returns:
        A new WorkflowState instance.
    """
    if prev_state is None:
        base_version = 0
        base_history: list[HistoryEntry] = []
        base_pinned: list[int] = []
        base_context: dict[str, Any] = {}
        base_workflow = ""
        base_parent_wf = None
        base_parent_step = None
        base_issue = None
        base_pr = None
    else:
        base_version = prev_state.version
        base_history = list(prev_state.history)
        base_pinned = list(prev_state.pinned_comment_ids)
        base_context = dict(prev_state.current_context)
        base_workflow = prev_state.workflow
        base_parent_wf = prev_state.parent_workflow
        base_parent_step = prev_state.parent_workflow_step
        base_issue = prev_state.issue
        base_pr = prev_state.pull_request

    # Build history entry for the current step
    entry = HistoryEntry(
        step=step,
        agent=agent,
        summary=summary,
        comment_id=comment_id,
        commit_id=commit_id,
        artifacts=artifacts or [],
        pin=pin,
    )
    new_history = base_history + [entry]

    # Handle pinned comments
    new_pinned = list(base_pinned)
    if pin and comment_id:
        if comment_id not in new_pinned:
            new_pinned.append(comment_id)

    # Build metrics
    new_metrics = None
    if metrics_dict:
        new_metrics = StateMetrics.from_full_metrics_dict(metrics_dict)

    # Merge context updates
    new_context = dict(base_context)
    if context_updates:
        new_context.update(context_updates)

    # Determine parent workflow tracking
    new_parent_wf = parent_workflow if parent_workflow is not None else base_parent_wf
    new_parent_step = parent_workflow_step if parent_workflow_step is not None else base_parent_step

    # Preserve issue info across transitions (once set, it persists)
    new_issue = issue or base_issue

    # Preserve PR info across transitions (once set, it persists)
    new_pr = pull_request or base_pr

    return WorkflowState(
        version=base_version + 1,
        workflow=workflow or base_workflow,
        step=step,
        agent=agent,
        phase=phase,
        history=new_history,
        pinned_comment_ids=new_pinned,
        metrics=new_metrics,
        current_context=new_context,
        parent_workflow=new_parent_wf,
        parent_workflow_step=new_parent_step,
        issue=new_issue,
        pull_request=new_pr,
    )