from typing import Optional, Any

from config import POSITION_STATE_VERSION, StepRef, WorkflowPosition

from ...workflow_state import WorkflowState, SoftRetryState
from ...sub_domains.issue_info.issue_info import IssueInfo
from ...sub_domains.pull_request_info.pull_request_info import PullRequestInfo
from ...sub_domains.history_entry.history_entry import HistoryEntry
from ...sub_domains.state_metrics.state_metrics import StateMetrics
from ...sub_domains.step_resolution.step_resolution import StepResolution
from ...sub_domains.dispatch.dispatch import Dispatch
from ...sub_domains.function_call import FunctionCall

def _position_refs(position: Optional[dict]) -> list[StepRef]:
    """Normalize a position input to the flat v3 refs list (issue #638).

    Accepts the ``parse_position_env`` block (``to_state_dict`` shape), a
    refs list, or ``None`` (v1 position-less run) — one reader for every
    shape the build path can hand over.
    """
    if isinstance(position, (list, tuple)):
        return [StepRef.model_validate(r) for r in position if r]
    parsed = WorkflowPosition.from_state_dict(position)
    return parsed.refs if parsed else []


def _last_history_value(history: list[HistoryEntry], field: str) -> str:
    """Return the last non-empty value of ``field`` across history entries."""
    for entry in reversed(history):
        value = getattr(entry, field, "")
        if value:
            return value
    return ""


def _fallback_field(explicit: str, prev_state: Optional[WorkflowState], base_history: list[HistoryEntry], field: str) -> str:
    """Fallback chain for scalar state fields: explicit arg -> previous state
    value -> last non-empty history entry -> '' (issue #458)."""
    if explicit:
        return explicit
    if prev_state is None:
        return ""
    prev_value = getattr(prev_state, field, "") or ""
    if prev_value:
        return prev_value
    return _last_history_value(base_history, field)


def build_state(
    prev_state: Optional[WorkflowState],
    *,
    workflow: str = "",
    step: str = "",
    agent: str = "",
    phase: str = "",
    summary: str = "",
    comment_ids: Optional[list[int]] = None,
    artifacts: Optional[list[str]] = None,
    pin: bool = False,
    inject_context: bool = True,
    result: Optional[str] = None,
    metrics_dict: Optional[dict[str, Any]] = None,
    context_updates: Optional[dict[str, Any]] = None,
    parent_workflow: Optional[str] = None,
    parent_workflow_step: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
    issue: Optional[IssueInfo] = None,
    pull_request: Optional[PullRequestInfo] = None,
    resolution: Optional[StepResolution] = None,
    dispatch: Optional[Dispatch] = None,
    function_call: Optional[FunctionCall] = None,
    position: Optional[dict] = None,
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
        comment_ids: GitHub comment IDs for this step's output (multi-part
            messages are posted as several comments, issue #504).
        artifacts: List of artifact names produced by this step.
        pin: If True, add all of this step's comment_ids to pinned_comment_ids.
        metrics_dict: Full metrics dict from the current agent run.
        context_updates: Key-value pairs to merge into current_context.
        parent_workflow: Parent workflow name (if in a sub-workflow).
        parent_workflow_step: Parent step name (if in a sub-workflow).
        scaffold_branch: Feature branch the branch-mounted scaffold plan was
            pushed to (issue #604). None preserves the previous state's
            value; a non-None value overwrites it (a re-scaffold updates it).
        issue: Issue metadata (once set, persists across transitions).
        pull_request: PR metadata (once set, persists across transitions).
        resolution: Step resolution context from resolve_config (replaced each transition).
        dispatch: Agent dispatch outcome from the AGENT_RUN_CONFIG sentinel
            (replaced each transition).
        function_call: Imperative agent-to-agent hand-off from the
            AGENT_RUN_CONFIG function_call section (replaced each transition;
            None clears any pending call).
        position: v3 flat workflow position (issue #638), parsed from the
            WORKFLOW_POSITION env via ``parse_position_env`` — the
            ``WorkflowPosition.to_state_dict()`` block. Non-empty positions
            are normalized to the flat refs list and recorded on both the new
            history entry and the state (``history[-1].position == position``
            invariant); a non-empty position also bumps the state version to
            POSITION_STATE_VERSION.

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
        base_scaffold_branch = None
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
        base_scaffold_branch = prev_state.scaffold_branch
        base_issue = prev_state.issue
        base_pr = prev_state.pull_request

    # Build history entry for the current step
    # Normalize the position block to the flat v3 refs list (issue #638);
    # the same refs are recorded on the entry and the state so
    # history[-1].position == state.position.
    position_refs = _position_refs(position)
    entry = HistoryEntry(
        step=step,
        agent=agent,
        summary=summary,
        comment_ids=comment_ids or [],
        artifacts=artifacts or [],
        pin=pin,
        inject_context=inject_context,
        result=result,
        position=position_refs,
        dispatch=dispatch,
    )
    new_history = base_history + [entry]

    # Handle pinned comments — a pinned multi-part entry pins all of its IDs
    new_pinned = list(base_pinned)
    for cid in comment_ids or []:
        if pin and cid not in new_pinned:
            new_pinned.append(cid)

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

    # Preserve scaffold branch across transitions (issue #604): None means
    # "no new scaffold info" (keep previous); a non-None value overwrites
    # (a re-scaffold updates the branch — last write wins).
    new_scaffold_branch = (
        scaffold_branch if scaffold_branch is not None else base_scaffold_branch
    )

    # Preserve per-step soft-kill retry accounting across transitions (issue
    # #704). The counter is default-filled from the previous state so a
    # pre-#704 blob (or a legacy blob without the key) starts empty; the
    # increment/reset is applied below once the effective step is known.
    base_soft_retry = (
        prev_state.soft_retry if prev_state is not None else SoftRetryState()
    )

    # State shape version (issue #638, universalized in #646): position is
    # now written on every run — top-level runs carry the bare single ref —
    # so the state is always at least the v3 shape. ``max`` keeps the
    # counter monotonic. V1 consumers are unaffected (flat fields keep leaf
    # semantics).
    state_version = max(POSITION_STATE_VERSION, base_version + 1)

    # Invariant: step/agent must not be silently dropped when a previous value
    # is recoverable (issue #458: empty WORKFLOW_STEP env produced states whose
    # scalar step disagreed with their own history).
    effective_step = _fallback_field(step, prev_state, base_history, "step")
    effective_agent = _fallback_field(agent, prev_state, base_history, "agent")

    # Per-step soft-kill retry accounting (issue #704). ``build_state`` is the
    # single writer of the counter; every mutation goes through the
    # ``SoftRetryState`` mutators so the increment/reset lifecycle has exactly
    # one owner:
    #   - retry path: the *previous* run was soft-killed, so this run is the
    #     retry that consumes one attempt for its step (``record``);
    #   - advance path: a normal transition into a different step resets the
    #     counter for the new step (``reset_for``).
    if (
        prev_state is not None
        and prev_state.dispatch is not None
        and prev_state.dispatch.status == "soft_kill"
    ):
        new_soft_retry = base_soft_retry.record(effective_step)
    elif base_soft_retry.step is not None and base_soft_retry.step != effective_step:
        new_soft_retry = base_soft_retry.reset_for(effective_step)
    else:
        new_soft_retry = base_soft_retry

    return WorkflowState(
        version=state_version,
        workflow=workflow or base_workflow,
        step=effective_step,
        agent=effective_agent,
        phase=phase,
        history=new_history,
        pinned_comment_ids=new_pinned,
        metrics=new_metrics,
        current_context=new_context,
        parent_workflow=new_parent_wf,
        parent_workflow_step=new_parent_step,
        scaffold_branch=new_scaffold_branch,
        issue=new_issue,
        pull_request=new_pr,
        resolution=resolution,
        dispatch=dispatch,
        function_call=function_call,
        position=position_refs,
        soft_retry=new_soft_retry,
    )
