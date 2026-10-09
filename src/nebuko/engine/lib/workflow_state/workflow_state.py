from pydantic import BaseModel, Field, model_validator
from typing import Any, Optional

from config import StepRef, WorkflowPosition

# Import domain entities first (no circular dependency)
from .sub_domains.history_entry.history_entry import HistoryEntry
from .sub_domains.issue_info.issue_info import IssueInfo
from .sub_domains.pull_request_info.pull_request_info import PullRequestInfo
from .sub_domains.dispatch.dispatch import Dispatch
from .sub_domains.function_call import FunctionCall
from .sub_domains.step_resolution.step_resolution import StepResolution

STATE_MARKER_START = "<!-- WORKFLOW_STATE_START -->"
STATE_MARKER_END = "<!-- WORKFLOW_STATE_END -->"

class SoftRetryState(BaseModel):  # @stub-protected
    """Per-step soft-kill retry accounting (issue #704).

    The soft time-kill (the ~20 min wind-down) interrupts the running agent
    before it finishes its step. The engine must re-run that step in place,
    bounded by the ``MAX_SOFT_RETRIES`` workflow parameter, instead of
    advancing. This model records, per step, how many soft retries have already
    been consumed so the router can decide between retrying and halting for a
    human.

    Fields:
        step: The step the counter applies to (None when no step has consumed
            a retry yet).
        attempts: Number of soft retries consumed for that step.
    """

    step: Optional[str] = None  # @stub-protected
    attempts: int = 0  # @stub-protected

    def record(self, step: str) -> "SoftRetryState":  # @stub-protected
        """Return the counter advanced by one soft retry for ``step``.

        Sole owner of the retry increment (issue #704). Called by
        :func:`build_state` on the **engine-retry path** — the previous run was
        soft-killed and this run was explicitly re-dispatched by the engine
        (``soft_retry_attempt`` marker), so it is the retry that consumes one
        attempt. A counter recorded for a different step is restarted at ``1``
        for ``step``.

        Args:
            step: Workflow step the retry is being consumed for.

        Returns:
            A new SoftRetryState with ``step`` set to ``step`` and ``attempts``
            advanced by one.
        """
        if self.step == step:
            return self.model_copy(update={"attempts": self.attempts + 1})
        return SoftRetryState(step=step, attempts=1)

    def reset_for(self, step: str) -> "SoftRetryState":  # @stub-protected
        """Return the counter reset for a normal transition into ``step``.

        Sole owner of the retry reset (issue #704). Called by
        :func:`build_state` on the **advance path** — a normal (non-soft-kill)
        transition that enters a different step. A counter already recorded for
        ``step`` is returned untouched so an in-progress retry budget is not
        wiped when the same step simply re-runs normally.

        Args:
            step: Workflow step being entered.

        Returns:
            ``self`` when the counter already applies to ``step``; otherwise a
            fresh SoftRetryState for ``step`` with ``attempts == 0``.
        """
        if self.step == step:
            return self
        return SoftRetryState(step=step, attempts=0)


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
            Each entry carries the metrics of its own step (``history[-1].metrics``
            is the single metrics authority; issue #792).
        pinned_comment_ids: GitHub comment IDs whose full content should be
            fetched at runtime for high-fidelity context.
        current_context: Free-form context dict for shared data between agents
            (pending tasks, constraints, decisions, etc.).
        parent_workflow: Parent workflow name if currently in a sub-workflow.
        parent_workflow_step: Step in parent that triggered the sub-workflow.
        scaffold_branch: Feature branch the branch-mounted scaffold plan was
            pushed to (issue #604). Set by the engine when the scaffold step
            finishes; used as the fetch-on-miss ref so a sub-workflow file
            that only exists on that branch (e.g. tmp_impl/workflow.yaml)
            can still be resolved. None until the scaffold step has run.
        issue: Static issue metadata (number, URL, title, author, repo).
        pull_request: Static PR metadata (number, URL, title, author, repo).
        position: Flat workflow step path, outermost-first; last ref = the
            step actually executed (issue #638). The flat ``workflow``/
            ``step`` fields keep leaf (v1) semantics.
        soft_retry: Per-step soft-kill retry accounting (issue #704). Tracks
            how many soft retries the current step has consumed so routing can
            retry the step in place (bounded by ``MAX_SOFT_RETRIES``) instead
            of advancing. Defaults to an empty counter, so legacy state blobs
            without this key load unchanged.
        callback: Optional return address (issue #765). When this agent was
            reached through a ``function_call``, this is the caller to return
            to once the agent finishes without an explicit decision of its own
            (the agent-as-tools pattern). None for workflow-driven runs, so
            legacy blobs without this key load unchanged.
        started_by: Login of whoever *started* the workflow (issue #795),
            captured from the first run's trigger author and carried forward
            verbatim by every later transition. It is the target of the
            engine's human-action notices, so a chained run (whose own trigger
            is a bot comment) still pings the human who is on the hook.
    """

    version: int = 1
    workflow: str = ""
    step: str = ""
    agent: str = ""
    phase: str = ""

    history: list[HistoryEntry] = Field(default_factory=list)
    pinned_comment_ids: list[int] = Field(default_factory=list)
    current_context: dict[str, Any] = Field(default_factory=dict)
    parent_workflow: Optional[str] = None
    parent_workflow_step: Optional[str] = None
    scaffold_branch: Optional[str] = None
    issue: Optional[IssueInfo] = None
    pull_request: Optional[PullRequestInfo] = None
    dispatch: Optional[Dispatch] = None
    resolution: Optional[StepResolution] = None
    # Imperative one-shot hand-off (AGENT_RUN_CONFIG function_call section).
    # Consumed by the next run's resolver, which emits a fresh state with it
    # cleared so it cannot re-fire (consumption by supersession).
    function_call: Optional[FunctionCall] = None
    # Return address for a function-call callee (issue #765): the caller to
    # fall back to when this agent finishes without an explicit decision.
    # Cleared on every non-function-call transition so it cannot re-fire.
    callback: Optional[str] = None
    # Login of whoever started the workflow (issue #795). Captured once (first
    # run) then carried forward; None on legacy blobs, which load unchanged.
    started_by: Optional[str] = None
    # Flat workflow step path, outermost-first, last ref = the step actually
    # executed (issue #638). Legacy v2 nested position blocks in posted
    # states are migrated to the flat refs by the validator below.
    position: list[StepRef] = Field(default_factory=list)
    # Per-step soft-kill retry accounting (issue #704). default_factory keeps
    # legacy blobs without this key loadable and byte-identical on read.
    soft_retry: SoftRetryState = Field(default_factory=SoftRetryState)  # @stub-protected

    @model_validator(mode="before")
    @classmethod
    def _migrate_position(cls, data: Any) -> Any:
        """Coerce legacy position shapes to the flat v3 refs list (issue #638).

        Accepts: a list of refs (already-typed or raw dicts — the normal v3
        shape), the ``to_state_dict`` block (``refs`` key / legacy v2 nested
        chain), or absent (v1 -> empty list). Operates on a copy so the
        caller's raw dict (e.g. a freshly ``json.load``-ed state file) is
        never mutated — it must stay JSON-serializable after validation.
        """
        if not isinstance(data, dict) or "position" not in data:
            return data
        new_data = dict(data)
        value = new_data.pop("position")
        if isinstance(value, (list, tuple)):
            refs = [StepRef.model_validate(r) for r in value if r]
        else:
            position = WorkflowPosition.from_state_dict(value)
            refs = position.refs if position else []
        new_data["position"] = refs
        return new_data

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
    comment_ids: Optional[list[int]] = None,
    artifacts: Optional[list[str]] = None,
    pin: bool = False,
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
    callback: Optional[str] = None,
) -> WorkflowState:
    from .features.build_state.build_state import build_state
    return build_state(
        prev_state=prev_state,
        workflow=workflow,
        step=step,
        agent=agent,
        phase=phase,
        summary=summary,
        comment_ids=comment_ids,
        artifacts=artifacts,
        pin=pin,
        result=result,
        metrics_dict=metrics_dict,
        context_updates=context_updates,
        parent_workflow=parent_workflow,
        parent_workflow_step=parent_workflow_step,
        scaffold_branch=scaffold_branch,
        issue=issue,
        pull_request=pull_request,
        resolution=resolution,
        dispatch=dispatch,
        function_call=function_call,
        callback=callback,
    )
