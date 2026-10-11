from pydantic import BaseModel, Field, model_validator
from typing import Any, Mapping, Optional

from config import (
    POSITION_STATE_VERSION,
    ComplexityPolicy,
    StepRef,
    WorkflowPosition,
)

# Import domain entities first (no circular dependency)
from .sub_domains.history_entry.history_entry import HistoryEntry
from .sub_domains.issue_info.issue_info import IssueInfo
from .sub_domains.pull_request_info.pull_request_info import PullRequestInfo
from .sub_domains.dispatch.dispatch import Dispatch
from .sub_domains.gate_decision.gate_decision import GateDecision
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


class CircuitBreakerState(BaseModel):  # @stub-protected
    """Per-issue circuit-breaker policy persisted in workflow_state (issue #839).

    The breaker is a stateless mechanism: every run recounts the bot markers
    and compares the count to an effective threshold. This model is the
    per-issue *policy source* for that threshold, letting a human raise (or
    tighten) the call budget for a single issue without touching the
    ``circuit_breaker_threshold`` workflow parameter.

    Fields:
        limit: Per-issue override. ``None`` means "inherit the workflow
            parameter" (``CIRCUIT_BREAKER_THRESHOLD``, effective default 25),
            so a state blob predating this feature never silently regresses a
            host running with a custom parameter.
    """

    limit: Optional[int] = None  # @stub-protected

    def effective(self, env_default: int) -> int:  # @stub-protected
        """Resolve the threshold: per-issue override wins, else ``env_default``.

        Args:
            env_default: The workflow-parameter threshold (the untouched
                ``CIRCUIT_BREAKER_THRESHOLD`` value, default 25).

        Returns:
            The override when set, otherwise ``env_default``.
        """
        return self.limit if self.limit is not None else env_default

    def with_limit(self, value: int) -> "CircuitBreakerState":  # @stub-protected
        """Return a new policy carrying ``value`` as the per-issue limit.

        Immutable replace mirroring :meth:`SoftRetryState.record`, so callers
        (the ``update circuit-breaker`` command) never mutate shared state.

        Args:
            value: The new per-issue limit (a positive integer).

        Returns:
            A new :class:`CircuitBreakerState` with ``limit=value``.
        """
        return self.model_copy(update={"limit": value})


# Payload shape generation of the state document (issue #821). Distinct from
# the ``WorkflowPosition`` block's own ``version`` (that one is the position
# block's shape marker, pinned to ``POSITION_STATE_VERSION``). Bumped when the
# top-level state shape changes: v1 flat -> v2 recursive position -> v3
# universal position -> v4 (``run_id`` + ``schema_version`` split).
STATE_SCHEMA_VERSION = 4


class WorkflowState(BaseModel):
    """
    Structured workflow state embedded in GitHub comments.

    Provides typed, machine-parseable state management replacing the previous
    HTML-comment-based phase detection. Every agent step produces a fresh
    full-snapshot state (Strategy 1 + summarization).

    Fields:
        run_id: Monotonically increasing invocation counter — the Nth state
            update in this workflow run (issue #821). Carried across the
            trigger->agent hand-off so an anomaly (a reset) is detectable.
        schema_version: Payload shape generation (issue #821). The
            ``STATE_SCHEMA_VERSION`` this state was written at. Defaults to
            ``0`` on a bare model; a legacy blob that still carries the
            conflated ``version`` key is read as generation ``3`` (its actual
            shape).
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
        gate_decision: Latest engine-owned human gate decision (issue #648),
            written from an identity-verified ``/nebuko approve gate`` /
            ``/nebuko reject gate`` command. None on legacy blobs without the
            key.
        gate_history: Full append-only audit of every gate decision for this
            workflow run, oldest first. Empty on legacy blobs without the key.
        position: Flat workflow step path, outermost-first; last ref = the
            step actually executed (issue #638). The flat ``workflow``/
            ``step`` fields keep leaf (v1) semantics.
        soft_retry: Per-step soft-kill retry accounting (issue #704). Tracks
            how many soft retries the current step has consumed so routing can
            retry the step in place (bounded by ``MAX_SOFT_RETRIES``) instead
            of advancing. Defaults to an empty counter, so legacy state blobs
            without this key load unchanged.
        circuit_breaker: Per-issue circuit-breaker override (issue #839). When
            set, it wins over the ``circuit_breaker_threshold`` workflow
            parameter for this issue; when unset the parameter (default 25)
            applies. Defaults to an empty policy, so legacy blobs without this
            key load unchanged and serialize byte-identically.
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
        workflow_ref: Pinned ref (SHA, tag, or branch) of the reusable workflow
            that defined this run (issue #862), captured from ``job.workflow_ref``
            via the ``NEBUKO_WORKFLOW_REF`` env var. Provenance only — the
            checkout pin is resolved by the workflow YAML itself. None when the
            var is absent, so legacy blobs load and serialize unchanged.
    """

    run_id: int = 0
    schema_version: int = 0
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
    # Engine-owned human gate decision (issue #648): the latest recorded
    # decision plus the full append-only audit of every decision. Written
    # only by the gate-decision resolver from an identity-verified human
    # command; None/empty on legacy blobs, which keep loading unchanged.
    gate_decision: Optional[GateDecision] = None
    gate_history: list[GateDecision] = Field(default_factory=list)
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
    # Pinned ref of the reusable workflow that defined this run (issue #862),
    # from ``NEBUKO_WORKFLOW_REF`` (``job.workflow_ref``). Provenance only;
    # None on legacy blobs, which load and serialize unchanged.
    workflow_ref: Optional[str] = None
    # Flat workflow step path, outermost-first, last ref = the step actually
    # executed (issue #638). Legacy v2 nested position blocks in posted
    # states are migrated to the flat refs by the validator below.
    position: list[StepRef] = Field(default_factory=list)
    # Per-step soft-kill retry accounting (issue #704). default_factory keeps
    # legacy blobs without this key loadable and byte-identical on read.
    soft_retry: SoftRetryState = Field(default_factory=SoftRetryState)  # @stub-protected
    # Per-issue circuit-breaker override (issue #839). default_factory keeps
    # legacy blobs without this key loadable and byte-identical on read; an
    # all-default policy serializes to nothing (exclude_defaults + collapse).
    circuit_breaker: CircuitBreakerState = Field(  # @stub-protected
        default_factory=CircuitBreakerState
    )
    # Set by the engine when the run's answer comment could not be delivered
    # (issue #805). Defaults to False so legacy blobs without this key load
    # unchanged; serialized only when True (``exclude_defaults``).
    publish_failed: bool = False  # @stub-protected
    # Resolved complexity policy snapshot (issue #882), pinned at run start for
    # auditability. Carries builtin->repo provenance. None on legacy blobs,
    # which load and serialize unchanged.
    complexity_policy: Optional[ComplexityPolicy] = None  # @stub-protected

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

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_version(cls, data: Any) -> Any:
        """Split the legacy conflated ``version`` into ``run_id`` /
        ``schema_version`` (issue #821).

        A single ``version`` field used to carry two meanings: the payload
        shape generation (floored at ``POSITION_STATE_VERSION``) and the
        state-update counter. Old blobs — posted comments, the
        ``$RUNNER_TEMP`` hand-off, fixtures — still carry it. Map it to the
        counter (``run_id``) so monotonicity across the trigger->agent
        boundary is preserved, and infer the legacy shape generation (v3)
        when ``schema_version`` is absent. Operates on a copy so the caller's
        raw dict (e.g. a freshly ``json.load``-ed state file) is never
        mutated.
        """
        if not isinstance(data, dict) or "version" not in data:
            return data
        new_data = dict(data)
        legacy = new_data.pop("version")
        new_data.setdefault("run_id", legacy)
        new_data.setdefault("schema_version", POSITION_STATE_VERSION)
        return new_data


def workflow_ref_from_env(env: Mapping[str, str]) -> Optional[str]:
    """Read the reusable workflow's pinned ref from ``NEBUKO_WORKFLOW_REF``.

    Provenance capture (issue #862): the checkout itself is pinned by the
    workflow YAML (``job.workflow_sha``), but the ref name that the caller
    requested (``job.workflow_ref``, e.g. ``octocat/hello-world/.github/
    workflows/nebuko-workflow.yaml@refs/tags/v1.2``) is only available inside
    the Actions runtime. Forwarding it lets a run record which workflow
    version produced it. An absent or empty value maps to ``None`` so a run
    outside the reusable-workflow host records nothing (backward compatible).

    Args:
        env: Environment mapping (normally ``os.environ``).

    Returns:
        The non-empty ``NEBUKO_WORKFLOW_REF`` value, or ``None``.
    """
    value = (env.get("NEBUKO_WORKFLOW_REF") or "").strip()
    return value or None


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
    workflow_ref: Optional[str] = None,
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
        workflow_ref=workflow_ref,
    )
