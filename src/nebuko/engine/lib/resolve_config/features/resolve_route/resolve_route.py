"""Routing feature.

The routing policy: given parsed state + trigger commands, decide which
path the next agent takes and produce the resolved config dict.
"""

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Optional

from config import (
    WorkflowDefinition,
    WorkflowStep,
    load_workflow,
    load_workflow_with_dirs,
    WorkflowPosition,
    StepPath,
    StepRef,
)
from resolve_workflow import (
    resolve_next_from_workflow,
    RouteKind,
    RouteResolution,
    resolve_sub_workflow_entry,
    _is_terminal_step,
    _load_workflow_by_reference,
    _load_child_workflow,
    resolve_workflow_reference,
    TASK_TO_PROMPT,
)
from workflow_state import (
    Appreciation,
    FunctionCall,
    GateDecision,
    GateLock,
    HoldKind,
    IncompleteReason,
    MAX_CONSULT_ROUNDS,
    RunPrefs,
    SUPPORTED_FUNCTIONS,
    TurnOutcome,
    TurnStatus,
    extract_state_from_body,
    extract_turn_outcome_from_state,
)

from ..fetch_state.fetch_state import _fetch_workflow_state_from_history
from ..parse_trigger.parse_trigger import (
    extract_mention_agent,
    extract_debug_flag,
    extract_machine_trigger,
    normalize_resume_input,
)
from ...sub_domains.author_identity.author_identity import (
    AuthorIdentity,
    build_author_identity,
)
from ...sub_domains.workflow_command.workflow_command import (
    WorkflowCommand,
    parse_workflow_command,
)
from ...sub_domains.workflow_entry.workflow_entry import WorkflowEntryPoint

__all__ = [
    "MAX_LOOP_COUNT",
    "resolve_next_agent_from_workflow",
    "count_consecutive_same_agent",
    "HandoffDecision",
    "_resolve_function_call_path",
    "_resolve_handoff_decision",
    "_resolve_machine_trigger_path",
    "_resolve_gate_decision_path",
]

# Regex for safe workflow names: alphanumeric, hyphens, underscores only
_SAFE_WF_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Maximum consecutive same-agent loops before blocking (loop safeguard)
MAX_LOOP_COUNT = 5


def resolve_next_agent_from_workflow(
    task_hint: Optional[str],
    workflow_name: str,
    step_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> Optional[str]:
    """Resolve next agent from workflow YAML definition.

    If task_hint is provided, use it directly. Otherwise, look up the
    workflow YAML to find the `next` agent for the current step.

    Args:
        task_hint: Optional explicit next agent override.
        workflow_name: Name of the active workflow.
        step_name: Current step name.
        workflows_dir: Path to workflows directory.
        local_workflows_dir: Optional consumer-local workflows dir that takes
            precedence over ``workflows_dir`` when it holds the file.

    Returns:
        Next agent name or None if unresolvable.
    """
    if task_hint:
        return task_hint

    if not workflow_name or not step_name:
        return None

    try:
        wf_file = load_workflow_with_dirs(workflow_name, workflows_dir, local_workflows_dir)
    except (FileNotFoundError, ValueError):
        return None
    wf_def = load_workflow(str(wf_file))

    for step in wf_def.steps:
        if step.name == step_name and step.next:
            # Find the agent for the next step
            for next_step in wf_def.steps:
                if next_step.name == step.next:
                    return next_step.agent
            return None
    return None


def count_consecutive_same_agent(history: list, current_agent: str) -> int:
    """Count consecutive same-agent entries from the end of history.

    Args:
        history: List of HistoryEntry objects.
        current_agent: Name of the current agent.

    Returns:
        Count of consecutive same-agent entries at the end of history.
    """
    count = 0
    for entry in reversed(history):
        if entry.agent == current_agent:
            count += 1
        else:
            break
    return count


def _resolve_resume_path(
    prev_state: "WorkflowState",
    trigger_body: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
    human_input: str = "",
) -> dict:
    """Resolve config to re-run the currently held step after /nebuko resume.

    Validates that the recorded turn outcome is a resumable hold — a
    ``human_gate`` hold (``awaiting_approval``), an ``awaiting_input`` hold, a
    soft-timeout exhaustion halt (issue #704) or an agent checkpoint (issue
    #965) — looks up the current step in the workflow YAML, then builds a
    config for the same step/agent (replaying so the agent can process the
    human's response). Copies the run preferences from the previously stored
    state. Injects human input into current_context so the agent prompt can
    reference it.

    Args:
        prev_state: WorkflowState containing the held workflow/step/agent.
        trigger_body: Raw trigger body containing the /nebuko resume command.
        workflows_dir: Path to workflows directory.
        local_workflows_dir: Optional consumer-local dir with precedence
            over ``workflows_dir`` (issue #529).

    Returns:
        Config dict for re-running the held step, or SKIP_AGENT dict.
    """
    outcome = prev_state.turn_outcome
    prefs = prev_state.prefs

    # Both a human-gate ``hold`` and a soft-kill / agent-checkpoint exhaustion
    # halt (issues #704, #965) are resumable: an explicit human resume re-runs
    # the interrupted step.
    if outcome is None or outcome.status != TurnStatus.COMPLETED:
        if outcome is None or outcome.incomplete_reason not in (
            IncompleteReason.SOFT_TIMEOUT,
            IncompleteReason.AGENT_CHECKPOINT,
        ):
            return {
                "SKIP_AGENT": "true",
                "REASON": (
                    "resume: step is not on hold "
                    f"(turn_outcome='{outcome.status.value if outcome else 'none'}')"
                ),
            }
    elif outcome.appreciation != Appreciation.HOLD:
        return {
            "SKIP_AGENT": "true",
            "REASON": (
                "resume: step is not on hold "
                f"(appreciation='{outcome.appreciation.value if outcome.appreciation else 'none'}')"
            ),
        }

    # A human_gate hold is released ONLY by the dedicated gate command
    # (issue #648): /nebuko resume is a non-release path here, so it stays
    # held and points the human at the correct command. This keeps a gated
    # step's transition an engine-owned state mutation driven by a typed,
    # identity-verified decision rather than a resume re-invocation.
    if _is_human_gate_hold(outcome):
        return {
            "SKIP_AGENT": "true",
            "REASON": (
                f"resume: step '{prev_state.step}' is held by a human gate — "
                "use `/nebuko approve gate` or `/nebuko reject gate` to release it"
            ),
        }

    wf_name = prev_state.workflow
    if not wf_name or not _SAFE_WF_NAME_RE.match(wf_name):
        return {
            "SKIP_AGENT": "true",
            "REASON": f"resume: invalid or missing workflow name '{wf_name}'",
        }

    try:
        wf_file = load_workflow_with_dirs(wf_name, workflows_dir, local_workflows_dir)
        wf_def = load_workflow(str(wf_file))
    except (FileNotFoundError, ValueError) as e:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"resume: workflow '{wf_name}' not found ({e})",
        }

    current_step_in_wf = prev_state.step
    if not current_step_in_wf:
        return {
            "SKIP_AGENT": "true",
            "REASON": "resume: no current step in workflow state",
        }

    # Find the current step definition to get its agent
    step_def = None
    for step in wf_def.steps:
        if step.name == current_step_in_wf:
            step_def = step
            break

    if step_def is None:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"resume: step '{current_step_in_wf}' not found in workflow '{wf_name}'",
        }

    if not step_def.agent:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"resume: step '{current_step_in_wf}' has no agent defined",
        }

    # Human input for the re-run. A bare resume (no feedback text) maps to
    # the BARE_RESUME_INPUT sentinel so the human_gate interceptor
    # (issue #506) recognizes this run as human-triggered and lets the
    # re-run's advance pass the gate.
    if not human_input:
        wf_cmd = parse_workflow_command(trigger_body)
        human_input = normalize_resume_input(
            wf_cmd.params.get("feedback", "") if wf_cmd is not None else None
        )

    # Copy the run preferences from the previous state so the step re-runs
    # with the same LLM settings (model, temperature, etc.)
    config = {
        "task": step_def.agent,
        "config_complexity": prefs.complexity if prefs else "",
        "config_reasoning": "none",
        "config_temperature": (
            str(prefs.temperature) if prefs and prefs.temperature is not None else ""
        ),
        "config_round": "",
        "config_model": (prefs.model or "") if prefs else "",
        "config_skills": (prefs.skills or "") if prefs else "",
        "config_top_p": (
            str(prefs.top_p) if prefs and prefs.top_p is not None else ""
        ),
        "config_top_k": (
            str(prefs.top_k) if prefs and prefs.top_k is not None else ""
        ),
        "config_repetition_penalty": (
            str(prefs.repetition_penalty)
            if prefs and prefs.repetition_penalty is not None
            else ""
        ),
        "config_debug": str(prefs.debug).lower() if prefs and prefs.debug else "",
        "config_workflow": wf_name,
        "config_step": current_step_in_wf,
        "config_branch": "",
        "config_parent_workflow": prev_state.parent_workflow or "",
        "config_parent_workflow_step": prev_state.parent_workflow_step or "",
        "config_human_resume_input": human_input,
    }

    return config


#: CI event author-identity env vars (wired in nebuko-workflow.yaml, issue
#: #648 step 8). Identity is sourced only from the GitHub event — never from
#: comment body markers, which are agent-writable and cannot attest authorship.
_AUTHOR_LOGIN_ENV = "TRIGGER_AUTHOR_LOGIN"
_AUTHOR_TYPE_ENV = "TRIGGER_AUTHOR_TYPE"
_AUTHOR_ASSOCIATION_ENV = "TRIGGER_AUTHOR_ASSOCIATION"
#: Optional deciding-comment id for the audit link.
_TRIGGER_COMMENT_ID_ENV = "TRIGGER_COMMENT_ID"


def build_author_identity_from_env(trigger_body: str) -> AuthorIdentity:
    """Build an ``AuthorIdentity`` from the CI event env (issue #648).

    Reads the login/type/association forwarded by CI. They default to empty
    when not wired yet, which keeps ``may_decide_gate`` default-deny (an empty
    association is not in the allowlist).
    """
    return build_author_identity(
        login=os.environ.get(_AUTHOR_LOGIN_ENV, "").strip(),
        user_type=os.environ.get(_AUTHOR_TYPE_ENV, "").strip(),
        association=os.environ.get(_AUTHOR_ASSOCIATION_ENV, "").strip(),
        trigger_body=trigger_body,
    )


def _trigger_comment_id() -> Optional[int]:
    """Deciding comment id from the CI env, or None when unavailable."""
    raw = os.environ.get(_TRIGGER_COMMENT_ID_ENV, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_human_gate_hold(outcome: Optional[TurnOutcome]) -> bool:
    """True when the turn outcome parks the step on its ``human_gate``.

    A hold is a *step-config fact* (issue #964): the workflow step declares
    ``human_gate: true``, so the outcome carries an ``awaiting_approval`` hold
    and the step stays parked until an identity-verified gate command releases
    it. The legacy ``human_gate:<step>`` reason string (and its holds) are gone.
    """
    return bool(
        outcome is not None
        and outcome.appreciation == Appreciation.HOLD
        and outcome.hold_kind == HoldKind.AWAITING_APPROVAL
    )


def _human_gate_hold_result(step: str, note: str = "") -> dict:
    """The SKIP_AGENT result that keeps a gated step parked (issues #887, #1003).

    A persisted ``human_gate`` hold must block auto-advance on every routing
    path until an identity-verified ``/nebuko approve gate`` /
    ``/nebuko reject gate`` releases it, so this names the gate step and the
    two commands that can release it.

    The verdict carries ``SKIP_KIND: human_gate_hold`` (issue #1003): without
    it the guard phase classified the skip as ``CONFIG_ERROR`` and exited 20
    instead of the deliberate hold exit 10. ``note`` appends an optional
    human-facing hint (e.g. a spent consult budget).
    """
    reason = (
        f"human_gate:{step} held — use /nebuko approve gate or "
        "/nebuko reject gate"
    )
    if note:
        reason = f"{reason} ({note})"
    return {
        "SKIP_AGENT": "true",
        "SKIP_KIND": "human_gate_hold",
        "REASON": reason,
    }


def _armed_gate(prev_state: Optional["WorkflowState"]) -> Optional[GateLock]:
    """The root-level gate lock (issue #1003) when it is armed, else None."""
    gate = getattr(prev_state, "gate", None) if prev_state is not None else None
    return gate if gate is not None and gate.armed() else None


def _resolve_gate_consult(
    prev_state: "WorkflowState",
    gate: GateLock,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> Optional[dict]:
    """Route an agent-to-agent ``call_agent`` made from an armed gate (#1003).

    The gate blocks the workflow *advance*, never a delegation: a gated agent
    that calls another agent is consulting, so the callee runs and ``callback``
    returns to the emitter at the unchanged position — the gate stays armed.
    The consultation is bounded by :data:`MAX_CONSULT_ROUNDS`; on exhaustion
    the hand-off is recorded but not executed, and the caller falls back to the
    benign gate hold.

    Returns:
        The callee config dict, or ``None`` when there is no executable
        hand-off (no ``function_call``, an unsupported function, no callee, or
        an exhausted budget).
    """
    function_call = getattr(prev_state, "function_call", None)
    if function_call is None:
        return None
    if not gate.can_consult():
        print(
            f"Gate consult budget exhausted ({gate.consult_rounds}/"
            f"{MAX_CONSULT_ROUNDS}) — holding '{gate.step}' for a human decision",
            file=sys.stderr,
        )
        return None
    config = _resolve_function_call_path(
        function_call,
        workflows_dir,
        prev_state=prev_state,
        local_workflows_dir=local_workflows_dir,
    )
    if config.get("SKIP_AGENT") == "true":
        # A malformed hand-off (unsupported function, no callee) is a genuine
        # config error: surface it rather than masking it as a benign hold.
        return config
    callee = (function_call.agent_name or "").strip()
    print(
        f"Gate consult: '{gate.step}' armed — delegating to '{callee}' "
        f"({gate.consult_rounds + 1}/{MAX_CONSULT_ROUNDS})",
        file=sys.stderr,
    )
    return config


def _consult_exhausted_note(
    prev_state: Optional["WorkflowState"],
) -> str:
    """Human-facing hint when an armed gate refused a consult (#1003).

    Only produced when a hand-off was actually dropped (there is a pending
    ``function_call``): a plain re-trigger on an armed gate stays silent.
    """
    gate = _armed_gate(prev_state)
    if gate is None or gate.can_consult():
        return ""
    if getattr(prev_state, "function_call", None) is None:
        return ""
    return f"consult budget {gate.consult_rounds}/{MAX_CONSULT_ROUNDS} spent"


def _resolve_gate_decision_path(
    prev_state: Optional["WorkflowState"],
    wf_cmd: WorkflowCommand,
    author: AuthorIdentity,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve a ``/nebuko approve gate`` / ``/nebuko reject gate`` command.

    Engine-owned gate release (issue #648). Handled inline and never
    dispatched to an agent. Validation (all must pass): the workflow is held
    at a ``human_gate:<step>``; the author is an authorized human
    (``AuthorIdentity.may_decide_gate``, Policy A); no recorded decision
    already binds this exact hold (single-shot). On success a
    :class:`GateDecision` is built and the workflow routes per the locked
    decisions: approved → the gated step's ``on_result: approved`` successor;
    rejected with feedback → re-run the gated step itself (the decision rides
    along; the gate re-arms when the re-run completes); rejected without
    feedback → the ``on_result: rejected`` successor.

    Returns a routed config carrying ``config_gate_decision`` (serialized) so
    the consuming run persists it, or a ``SKIP_AGENT`` dict.
    """
    rejection = _gate_hold_rejection_reason(prev_state, author)
    if rejection:
        return {"SKIP_AGENT": "true", "REASON": rejection}

    decision = _build_gate_decision(prev_state, wf_cmd, author)
    config = _route_gate_decision(
        prev_state, decision, workflows_dir, local_workflows_dir
    )
    if config.get("SKIP_AGENT") == "true":
        return config
    config["config_gate_decision"] = decision.model_dump_json()
    # Release the root-level lock (issue #1003): the decision is the only
    # authorized release, so the lock must not survive it. A rejection with
    # feedback re-runs the gated step, whose next completing turn re-arms it.
    gate = getattr(prev_state, "gate", None) if prev_state is not None else None
    if gate is not None:
        config["config_gate_released"] = "true"
    return config


def _gate_hold_rejection_reason(
    prev_state: Optional["WorkflowState"],
    author: AuthorIdentity,
) -> Optional[str]:
    """Why this author/command may not decide the gate, or None when it may.

    Fail-closed: a missing state, a step not held at a gate, an unauthorized
    author, or a hold that already carries a decision (single-shot replay) all
    yield a reason and the command becomes inert.
    """
    if prev_state is None:
        return "gate: no prior workflow state found"
    if not _is_human_gate_hold(prev_state.turn_outcome):
        return "gate: workflow is not held at a human_gate step"
    if not author.may_decide_gate():
        return (
            f"gate: author '{author.login or 'unknown'}' is not authorized "
            "to decide a gate"
        )
    position = list(prev_state.position or [])
    if any(
        decision.is_for(prev_state.workflow, prev_state.step, position)
        for decision in (prev_state.gate_history or [])
    ):
        return "gate: this hold already has a recorded decision (single-shot)"
    return None


def _build_gate_decision(
    prev_state: "WorkflowState",
    wf_cmd: WorkflowCommand,
    author: AuthorIdentity,
) -> GateDecision:
    """Build the attributed decision for the current hold instance."""
    return GateDecision(
        status="approved" if wf_cmd.action == "approve" else "rejected",
        workflow=prev_state.workflow,
        target_step=prev_state.step,
        decided_by=author.login,
        decided_at=datetime.now(timezone.utc),
        feedback=wf_cmd.params.get("feedback", "") or "",
        comment_id=_trigger_comment_id(),
        position=list(prev_state.position or []),
    )


def _gate_base_config(prev_state: "WorkflowState") -> dict:
    """Empty routed config carrying the current workflow/step/parent context."""
    return {
        "task": "",
        "config_complexity": "",
        "config_reasoning": "none",
        "config_temperature": "",
        "config_round": "",
        "config_model": "",
        "config_skills": "",
        "config_top_p": "",
        "config_top_k": "",
        "config_repetition_penalty": "",
        "config_debug": "",
        "config_workflow": prev_state.workflow,
        "config_step": prev_state.step or "",
        "config_branch": "",
        "config_parent_workflow": prev_state.parent_workflow or "",
        "config_parent_workflow_step": prev_state.parent_workflow_step or "",
    }


def _route_gate_decision(
    prev_state: "WorkflowState",
    decision: GateDecision,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Route a validated decision to its target step (issue #648)."""
    config = _gate_base_config(prev_state)
    wf_name = prev_state.workflow
    step_name = prev_state.step
    if decision.status == "rejected" and decision.feedback:
        return _rerun_gated_step(
            config,
            wf_name,
            step_name,
            workflows_dir,
            local_workflows_dir,
            prev_state.scaffold_branch,
        )
    wf_result = resolve_next_from_workflow(
        wf_name,
        step_name,
        workflows_dir,
        branch_result=decision.status,
        parent_workflow=prev_state.parent_workflow or "",
        parent_workflow_step=prev_state.parent_workflow_step or "",
        local_workflows_dir=local_workflows_dir,
        scaffold_branch=prev_state.scaffold_branch,
        history=prev_state.history,
    )
    if wf_result is None or wf_result.kind in (
        RouteKind.DECLARED_FINISH,
        RouteKind.UNRESOLVED,
    ):
        return _workflow_next_step_skip_reason(wf_name, step_name, workflows_dir)
    if wf_result.skip_agent:
        return wf_result.skip_result()
    if wf_result.kind is RouteKind.RUN_SCRIPT:
        return _apply_gate_successor(config, wf_result, prev_state)
    _apply_route_resolution(config, wf_result, prev_state)
    return config


def _rerun_gated_step(
    config: dict,
    wf_name: str,
    step_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
    scaffold_branch: Optional[str] = None,
) -> dict:
    """Route a reject-with-feedback back to the gated step itself.

    The step's agent re-runs with the decision/feedback in context; the gate
    re-arms when the re-run completes, so fresh human validation is required.
    """
    try:
        wf_def = _load_workflow_by_reference(
            wf_name, workflows_dir, local_workflows_dir, scaffold_branch
        )
    except Exception as exc:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"gate: could not load workflow '{wf_name}' ({exc.__class__.__name__})",
        }
    step_def = _find_step(wf_def, step_name)
    if step_def is None or not step_def.agent:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"gate: gated step '{step_name}' is not runnable",
        }
    config["task"] = step_def.agent
    config["config_workflow"] = wf_name
    config["config_step"] = step_name
    return config


def _apply_gate_successor(
    config: dict,
    wf_result: RouteResolution,
    prev_state: "WorkflowState",
) -> dict:
    """Apply a script ``on_result`` successor (approved|rejected) to the config."""
    _apply_route_resolution(config, wf_result, prev_state)
    return config


def _sub_workflow_state_result(
    config: dict,
    wf_result: RouteResolution,
    prev_state: "WorkflowState",
) -> Optional[str]:
    """Apply a sub-workflow boundary transition to the resolved config.

    Entry: switch ``config_workflow``/``config_step`` to the sub-workflow's
    first step and record the parent reference for the return leg.
    Exit: restore the parent workflow, resume at the step after the
    sub-workflow reference, and clear the parent tracking fields.

    Returns ``None`` when the resolution is not a boundary transition, else the
    ``"entered"``/``"exited"`` action so the caller can reset its loop guard.
    """
    if wf_result.kind is RouteKind.SUB_WORKFLOW_ENTER:
        _apply_route_resolution(config, wf_result, prev_state)
        return "entered"
    if wf_result.kind is RouteKind.SUB_WORKFLOW_EXIT:
        _apply_route_resolution(config, wf_result, prev_state)
        return "exited"
    return None


def _callback_return_target(
    prev_state: Optional["WorkflowState"],
    outcome: Optional[TurnOutcome],
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """Resolve the function-call return target for a finishing callee (#765).

    When an agent was reached through a ``function_call`` (the agent-as-tools
    pattern) its state carries the caller as a ``callback``. A callee that
    finishes with a ``hold`` verdict of its own routes on that verdict; a callee
    that completed without a destination of its own returns to its caller
    instead of continuing the workflow.

    Returns ``(agent, step)`` when the callback should route the run, else
    ``None``. The callback is an *advance-only* fallback: an explicit
    ``required_agent`` hand-off or a deliberate ``hold`` always wins, which is
    the precedence documented in ``docs/handoff-and-callback.md``.
    """
    if prev_state is None:
        return None
    callback = (getattr(prev_state, "callback", "") or "").strip()
    if not callback:
        return None
    if callback == (getattr(prev_state, "agent", "") or "").strip():
        # Never re-enter the agent that just ran (defensive; self-calls are
        # blocked upstream).
        return None
    if outcome is not None and outcome.appreciation == Appreciation.HOLD:
        # A deliberate stop is never redirected to the callback: it routes on
        # its own verdict (issue #964).
        return None
    if outcome is not None and outcome.required_agent:
        # The callee named its own explicit destination: it outranks callback.
        return None
    wf_name = (getattr(prev_state, "workflow", "") or "").strip()
    if not wf_name:
        return (callback, "")
    target_step = _resolve_callee_step(
        wf_name,
        _recover_current_step(prev_state),
        callback,
        workflows_dir,
        local_workflows_dir,
    )
    if not target_step:
        # No workflow step for the callback: fall through to the workflow
        # forward edge rather than emitting a bare callback into a workflow.
        return None
    return (callback, target_step)


def _route_config_from_prefs(
    prev_state: "WorkflowState",
    prefs: Optional[RunPrefs],
) -> dict:
    """Base routed config: the active position plus the run preferences."""
    return {
        "task": "",
        "config_complexity": (prefs.complexity if prefs else "") or "",
        "config_reasoning": "none",
        "config_temperature": (
            str(prefs.temperature) if prefs and prefs.temperature is not None else ""
        ),
        "config_round": "",
        "config_model": (prefs.model or "") if prefs else "",
        "config_skills": (prefs.skills or "") if prefs else "",
        "config_top_p": str(prefs.top_p) if prefs and prefs.top_p is not None else "",
        "config_top_k": str(prefs.top_k) if prefs and prefs.top_k is not None else "",
        "config_repetition_penalty": (
            str(prefs.repetition_penalty)
            if prefs and prefs.repetition_penalty is not None
            else ""
        ),
        "config_debug": str(prefs.debug).lower() if prefs and prefs.debug else "",
        "config_workflow": prev_state.workflow or "",
        "config_step": prev_state.step or "",
        "config_branch": "",
        "config_parent_workflow": prev_state.parent_workflow or "",
        "config_parent_workflow_step": prev_state.parent_workflow_step or "",
    }


def _step_declares_human_gate(
    prev_state: "WorkflowState",
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> bool:
    """True when the current step is declared ``human_gate: true`` (issue #964).

    The gate is a step-config fact: an agent that completes a gate step without
    a verdict of its own parks the step rather than auto-advancing past it.
    """
    wf_name = (prev_state.workflow or "").strip()
    step_name = (prev_state.step or "").strip()
    if not (wf_name and step_name):
        return False
    return _is_human_gate_step(wf_name, step_name, workflows_dir, local_workflows_dir)


def _apply_route_resolution(
    config: dict,
    wf_result: Optional[RouteResolution],
    prev_state: "WorkflowState",
) -> None:
    """Project a typed workflow resolution onto the routed config (issue #964).

    ``None`` (workflow/step did not resolve) and the skip/declared-finish kinds
    leave the config at its current position; every other kind carries the
    successor's task/step and the sub-workflow boundary markers.
    """
    if wf_result is None or wf_result.skip_agent:
        return
    if wf_result.kind is RouteKind.RUN_SCRIPT:
        # Deterministic script successor (issue #541): carry the script payload
        # instead of an agent task; build_result short-circuits.
        config["run_script"] = dict(wf_result.script or {})
        config["config_step"] = wf_result.step or config["config_step"]
        config["config_workflow"] = prev_state.workflow
        return
    if wf_result.kind is RouteKind.ROUTE:
        config["task"] = wf_result.task
        config["config_step"] = wf_result.step or config["config_step"]
        config["config_workflow"] = prev_state.workflow
        if wf_result.branch:
            config["config_branch"] = wf_result.branch
        return
    if wf_result.kind is RouteKind.SUB_WORKFLOW_ENTER:
        config["task"] = wf_result.task
        config["config_step"] = wf_result.step or config["config_step"]
        config["config_workflow"] = wf_result.workflow
        config["config_parent_workflow"] = wf_result.parent_workflow
        config["config_parent_workflow_step"] = wf_result.parent_workflow_step
        if wf_result.branch:
            config["config_branch"] = wf_result.branch
        return
    if wf_result.kind is RouteKind.SUB_WORKFLOW_EXIT:
        config["task"] = wf_result.task
        config["config_step"] = wf_result.step or config["config_step"]
        config["config_workflow"] = wf_result.workflow
        config["config_parent_workflow"] = ""
        config["config_parent_workflow_step"] = ""
        if wf_result.branch:
            config["config_branch"] = wf_result.branch


def _is_runnable_workflow_step(
    prev_state: "WorkflowState",
    workflows_dir: str,
    local_workflows_dir: Optional[str],
) -> bool:
    """True when the current claim names a step that really exists (issue #964).

    Distinguishes a genuine end-of-workflow (the step is declared and has no
    successor) from a resolution failure (unknown/missing workflow or step),
    which must ping for input rather than read as a finish.
    """
    wf_name = (prev_state.workflow or "").strip()
    step_name = (prev_state.step or "").strip()
    if not (wf_name and step_name):
        return False
    try:
        workflow = _load_workflow_by_reference(wf_name, workflows_dir, local_workflows_dir)
    except Exception:
        return False
    return _find_step(workflow, step_name) is not None


def _terminal_skip_result(
    prev_state: "WorkflowState",
    workflows_dir: str,
    local_workflows_dir: Optional[str],
    wf_result: Optional[RouteResolution] = None,
) -> Optional[dict]:
    """Declared end-of-workflow stop, or ``None`` when the claim is unresolved.

    The resolver's own kind is authoritative when it supplied one: an
    :attr:`RouteKind.UNRESOLVED` step declares forward edges that matched
    nothing, so it is a config gap owed a human ping, never an honest finish
    (the #923 round-2 dead-end). Only a :attr:`RouteKind.DECLARED_FINISH` —
    or no resolution at all, when the step simply has no onward edge — stops
    the run as a benign terminal.
    """
    if not _is_runnable_workflow_step(prev_state, workflows_dir, local_workflows_dir):
        return None
    if wf_result is not None and wf_result.kind is not RouteKind.DECLARED_FINISH:
        return None
    return {
        "SKIP_AGENT": "true",
        "REASON": "Terminal step reached",
        "SKIP_KIND": "terminal",
    }


def _hold_skip_result(outcome: TurnOutcome, prev_state: "WorkflowState") -> dict:
    """The skip for a ``hold`` verdict the engine cannot park (issue #964)."""
    if outcome.hold_kind == HoldKind.AWAITING_APPROVAL:
        return _human_gate_hold_result(prev_state.step or "")
    return {
        "SKIP_AGENT": "true",
        "REASON": f"Awaiting input: {outcome.note or 'no note'}",
    }


def _awaiting_input_result(
    prev_state: "WorkflowState",
    outcome: Optional[TurnOutcome],
) -> dict:
    """No verdict and no onward edge: ping for input, never fake a finish."""
    note = (outcome.note if outcome is not None else "") or ""
    detail = f" ({note})" if note else ""
    return {
        "SKIP_AGENT": "true",
        "REASON": (
            f"Resolution failed: step '{prev_state.step}' in workflow "
            f"'{prev_state.workflow}' resolved no successor{detail}"
        ),
    }


def _unmatched_verdict_result(
    outcome: TurnOutcome,
    prev_state: "WorkflowState",
    workflows_dir: str,
) -> dict:
    """A verdict that selected no declared edge (issue #964).

    The workflow either declares no ``on_result`` edge for this key or declares
    edges that all lead nowhere: the run must ping the human rather than post a
    workflow-complete.
    """
    key = outcome.route_key
    return {
        "SKIP_AGENT": "true",
        "REASON": (
            f"Resolution failed: step '{prev_state.step}' in workflow "
            f"'{prev_state.workflow}' has no route for verdict '{key}'"
        ),
    }


def _resolve_outcome_path(
    prev_state: "WorkflowState",
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve config from the state's factual turn outcome (issue #964).

    The routing policy, in order:

    * ``INCOMPLETE / SOFT_TIMEOUT | AGENT_CHECKPOINT`` — the interrupted step is
      retried in place while its per-step budget remains (soft-kill route).
    * ``COMPLETED / HOLD`` (or a step declared ``human_gate: true``) — park the
      step: human-gate hold, or an awaiting-input skip.
    * ``COMPLETED / APPROVED | REJECTED`` — the verdict is the route key and
      selects the workflow's ``on_result`` edge; ``required_agent`` overrides
      the agent at the routed step.
    * ``INCOMPLETE / FAILURE | INTERRUPTED | NO_OUTCOME`` — no route key: follow
      the linear/default workflow edge.

    Args:
        prev_state: WorkflowState from the trigger body.
        workflows_dir: Path to workflows directory.
        local_workflows_dir: Optional consumer-local workflows dir that takes
            precedence over ``workflows_dir`` (issue #529).

    Returns:
        Config dict with task and all config_* variables, or SKIP_AGENT dict.
    """
    outcome = prev_state.turn_outcome
    prefs = prev_state.prefs

    # Soft-kill must not advance (issue #704): a soft-killed step is retried in
    # place while its per-step budget remains, and halts for a human once it is
    # spent. An agent checkpoint (a ``handoff`` fold, issue #965) is routed
    # through the same path: the agent chose to stop, so the step re-runs under
    # the same per-step budget rather than advancing.
    if outcome is not None and outcome.incomplete_reason in (
        IncompleteReason.SOFT_TIMEOUT,
        IncompleteReason.AGENT_CHECKPOINT,
    ):
        soft_kill_config = resolve_soft_kill_route(
            prev_state, outcome, resolve_max_soft_retries()
        )
        if soft_kill_config is not None:
            return soft_kill_config

    # Human-gate hold precedence (issues #887, #964, #1003): a recorded gate
    # hold must block auto-advance even when the workflow forward edge
    # resolves. Checked before workflow-next resolution, which would otherwise
    # follow ``plan -> implement`` and silently ignore the gate. A pending
    # agent-to-agent hand-off is routed first: the gate blocks the workflow
    # *advance*, never a delegation (issue #1003).
    if _is_human_gate_hold(outcome):
        gate = _armed_gate(prev_state)
        if gate is not None:
            consult = _resolve_gate_consult(
                prev_state, gate, workflows_dir, local_workflows_dir
            )
            if consult is not None:
                return consult
            return _human_gate_hold_result(
                prev_state.step or "", _consult_exhausted_note(prev_state)
            )
        return _human_gate_hold_result(prev_state.step or "")

    # An agent that completed a gate step without a verdict of its own leaves
    # the gate armed: park the step for the human rather than advancing past it.
    if (
        outcome is not None
        and outcome.status == TurnStatus.COMPLETED
        and outcome.appreciation is None
        and _step_declares_human_gate(prev_state, workflows_dir, local_workflows_dir)
    ):
        gate = _armed_gate(prev_state)
        if gate is not None:
            consult = _resolve_gate_consult(
                prev_state, gate, workflows_dir, local_workflows_dir
            )
            if consult is not None:
                return consult
            return _human_gate_hold_result(
                prev_state.step or "", _consult_exhausted_note(prev_state)
            )
        return _human_gate_hold_result(prev_state.step or "")

    config = _route_config_from_prefs(prev_state, prefs)

    # Callback return (issue #765): when this run is a callee reached through a
    # caller's function_call, a completion with no target of its own returns to
    # the caller rather than continuing the workflow. Precedence: explicit
    # function_call (resolved upstream) > explicit required_agent > gate hold >
    # callback > workflow forward edge.
    callback_target = _callback_return_target(
        prev_state, outcome, workflows_dir, local_workflows_dir
    )
    if callback_target is not None:
        target_agent, target_step = callback_target
        config["task"] = target_agent
        config["config_workflow"] = prev_state.workflow
        config["config_step"] = target_step
        print(
            f"Callback: returning to agent '{target_agent}' "
            f"(workflow='{prev_state.workflow}', step='{target_step}')",
            file=sys.stderr,
        )
        return config

    # Workflow-driven resolution using previous workflow state
    if prev_state and prev_state.workflow and prev_state.step:
        wf_result = resolve_next_from_workflow(
            prev_state.workflow,
            prev_state.step,
            workflows_dir,
            agent_hint=None,
            branch_result=outcome.route_key if outcome is not None else None,
            parent_workflow=prev_state.parent_workflow or "",
            parent_workflow_step=prev_state.parent_workflow_step or "",
            local_workflows_dir=local_workflows_dir,
            scaffold_branch=getattr(prev_state, "scaffold_branch", None),
            history=prev_state.history,
        )
        _apply_route_resolution(config, wf_result, prev_state)
        if wf_result is not None and wf_result.skip_agent:
            # Surfaced mount-resolution failure (issue #671): a declared
            # sub-workflow mount that could not be resolved must stop the
            # run with a reason, not be silently stepped over.
            return wf_result.skip_result()
        if outcome is not None and outcome.appreciation == Appreciation.HOLD:
            return _hold_skip_result(outcome, prev_state)
        if config["task"]:
            return config
        if config.get("run_script"):
            return config
        if outcome is not None and outcome.appreciation is not None:
            # A verdict is a routing instruction: a verdict that selected no
            # declared edge is a config gap, never an honest finish (the #923
            # round-2 dead-end).
            return _unmatched_verdict_result(outcome, prev_state, workflows_dir)
        # No verdict and nothing onward: the step is terminal.
        terminal = _terminal_skip_result(
            prev_state, workflows_dir, local_workflows_dir, wf_result
        )
        if terminal is not None:
            return terminal
        if outcome is not None and outcome.status == TurnStatus.COMPLETED:
            # A completed gate step with no verdict leaves the gate armed.
            if _step_declares_human_gate(prev_state, workflows_dir, local_workflows_dir):
                return _human_gate_hold_result(prev_state.step or "")
        return _awaiting_input_result(prev_state, outcome)
    config["task"] = (outcome.required_agent if outcome else None) or prev_state.agent or ""
    return config


# Soft-kill retry budget fallback (issue #704): the hosting workflow may supply
# the budget through the ``MAX_SOFT_RETRIES`` environment variable; this is the
# value used when it is unset or malformed.
DEFAULT_MAX_SOFT_RETRIES = 2


def resolve_max_soft_retries(env: Optional[Mapping[str, str]] = None) -> int:  # @stub-protected
    """Read and validate the ``MAX_SOFT_RETRIES`` environment value (#704).

    Sole read site for the soft-kill retry budget. The value is exported as the
    ``MAX_SOFT_RETRIES`` environment variable by the hosting workflow; when it
    is absent or malformed the budget falls back to ``DEFAULT_MAX_SOFT_RETRIES``.

    Args:
        env: Mapping to read the variable from; ``None`` reads ``os.environ``.

    Returns:
        The validated budget: the declared value when it is a non-negative
        integer; ``DEFAULT_MAX_SOFT_RETRIES`` when the variable is absent or
        malformed; ``0`` when the declared value is negative (halt
        immediately rather than retry forever).
    """
    source = os.environ if env is None else env
    raw = source.get("MAX_SOFT_RETRIES")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MAX_SOFT_RETRIES
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_SOFT_RETRIES
    return max(value, 0)


def resolve_soft_kill_route(
    prev_state: "WorkflowState",
    outcome: TurnOutcome,
    max_soft_retries: int,
) -> Optional[dict]:  # @stub-protected
    """Decide the retry-vs-halt route for an interrupted step (issue #704).

    A soft time-kill (or an agent checkpoint, issue #965) must not let the
    workflow advance: the interrupted step is re-run in place while the
    per-step counter is within ``max_soft_retries``, and the run halts for a
    human once the retries are exhausted. ``_resolve_outcome_path`` calls this
    before its normal workflow-next resolution and returns the result as-is
    when it is not ``None``.

    Args:
        prev_state: WorkflowState from the trigger body; its flat
            ``workflow``/``step`` name the step to re-run and ``soft_retry``
            carries the retry count already consumed for that step.
        outcome: The state's turn outcome; only an INCOMPLETE outcome whose
            reason is ``soft_timeout`` or ``agent_checkpoint`` triggers
            soft-kill handling.
        max_soft_retries: ``MAX_SOFT_RETRIES`` workflow parameter — the number
            of retries allowed per step before halting for a human. Values
            ``< 0`` are treated as ``0`` (halt immediately).

    Returns:
        ``None`` when the outcome is not a soft-kill/checkpoint interruption
        (fall through to normal routing). Otherwise one of:

        * **retry** — a same-step resolution config dict carrying at least
          ``task=prev_state.agent`` (re-run the interrupted step's agent),
          ``config_step=prev_state.step``, ``config_workflow=prev_state.workflow``
          and the recorded run preferences, while the retries consumed for the
          step are below ``max_soft_retries``. The dict also carries the
          ``config_soft_retry_attempt="true"`` marker: it is the only signal
          that tells the state writer this run is an *engine* retry, so the
          per-step budget is consumed for retries and never for a human/manual
          trigger;
        * **halt** — ``{"SKIP_AGENT": "true", "REASON": ...}`` naming the step,
          the exhausted attempt count and the ``/nebuko resume`` command,
          once the budget is spent so a human can resume. It also carries
          ``SOFT_RETRY_EXHAUSTED="true"`` so the engine can tell this terminal
          skip apart from a routine one and post a human-visible halt notice
          (issue #793).

        This is a **pure reader**: the counter is never mutated here. The
        increment/reset lifecycle is owned by the workflow-state writer
        (``build_state`` → ``SoftRetryState.record``/``reset_for``). A counter
        recorded for a *different* step does not consume the current step's
        budget (treated as zero attempts).
    """
    if outcome.incomplete_reason not in (
        IncompleteReason.SOFT_TIMEOUT,
        IncompleteReason.AGENT_CHECKPOINT,
    ):
        return None

    step = prev_state.step or ""
    counter = prev_state.soft_retry
    attempts = counter.attempts if counter.step == prev_state.step else 0
    budget = max(max_soft_retries, 0)

    if attempts < budget:
        # Same-step retry: clone the recorded run preferences onto the
        # interrupted step. Mirrors the normal outcome-routing config surface so
        # ``build_result`` treats the retry exactly like an in-place re-run.
        # ``config_soft_retry_attempt`` marks the run as an engine retry so the
        # state writer consumes exactly one per-step attempt for it.
        config = _route_config_from_prefs(prev_state, prev_state.prefs)
        config["task"] = prev_state.agent or ""
        config["config_workflow"] = prev_state.workflow or ""
        config["config_step"] = step
        config["config_soft_retry_attempt"] = "true"
        return config

    return {
        "SKIP_AGENT": "true",
        "REASON": (
            f"Soft time-kill: step '{step}' exhausted its retry budget "
            f"({attempts}/{budget}); halting the workflow for a human to resume "
            f"with /nebuko resume."
        ),
        # Marker consumed by ``Engine.run`` (issue #793) so the terminal skip
        # posts a human-visible halt notice instead of staying silent.
        "SOFT_RETRY_EXHAUSTED": "true",
    }


def _mount_chain_ref_resolver(workflows_dir: str, local_workflows_dir: Optional[str] = None) -> Callable[[str], Optional[WorkflowDefinition]]:
    """Build a ``resolve_position`` ref port over the real workflow loader.

    The port maps a mount reference (name or repo-root-relative path) to the
    loaded :class:`WorkflowDefinition` or ``None`` when unresolvable. It
    carries the child's actual file path onto the definition as
    ``source_path`` so :func:`resolve_position` records the real location
    instead of guessing (issue #618, review fix: no name heuristic).
    """
    def resolve_ref(ref: str) -> Optional[WorkflowDefinition]:
        if not ref:
            return None
        try:
            wf = _load_workflow_by_reference(ref, workflows_dir, local_workflows_dir)
        except Exception:
            return None
        if wf is None:
            return None
        try:
            if "/" in ref or ref.endswith((".yaml", ".yml")):
                # Path-form reference: the loader resolved exactly this file
                # (issue #468 resolution rules).
                source_path = str(Path(resolve_workflow_reference(ref, workflows_dir)))
            else:
                # Name-form reference: mirror the loader's multi-directory
                # scan (consumer-local dir wins, issue #529) instead of the
                # old name heuristic (issue #618, review fix).
                try:
                    source_path = str(load_workflow_with_dirs(ref, workflows_dir, local_workflows_dir or ""))
                except Exception:
                    source_path = ""
            if source_path:
                object.__setattr__(wf, "source_path", source_path)
        except Exception:
            pass
        return wf
    return resolve_ref


# @stub-protected
@dataclass(frozen=True)
class HandoffDecision:
    """Single explicit outcome of 'where does the next invocation go', precedence applied."""

    target_agent: str
    target_step: str
    reason_code: Literal["dispatch", "function_call", "gate_hold"]

    # @stub-protected: signature defined by architect — do not modify
    def to_config(self) -> dict[str, str]:
        """Resolver config mapping: {'task': target_agent, 'step': target_step}."""
        return {"task": self.target_agent, "step": self.target_step}


# @stub-protected: signature defined by architect — do not modify
def _resolve_callee_step(wf_name: str, current_step: str, callee: str, workflows_dir: str, local_workflows_dir: Optional[str] = None, exclude_step: Optional[str] = None) -> str:
    """Resolve the workflow step that ``callee`` should run (issue #507).

    When a caller hands off via ``function_call`` while a workflow is active,
    the callee's actual workflow step must be established — otherwise the
    callee's posted state inherits the *caller's* stale step and the next
    re-trigger mis-resolves the next task to the callee itself, which the
    same-agent loop guard then skips (issue #506 repro).

    Resolution order (issue #618 defect 1):
      1. Linear successor of ``current_step`` whose ``agent == callee``
         (in-workflow hand-offs such as architect(analyze) -> context(context)).
      2. When ``current_step``'s successor is a *workflow-mount* step the
         callee enters through a sub-workflow: resolve the mount chain via
         :func:`resolve_position` and return the leaf step it lands on — the
         ``dev`` hand-off from ``scaffold`` must resolve to the mounted
         ``dev-review-loop / implement`` leaf, never the already-consumed
         ``scaffold`` (the #617 dead-end).
      3. Fallback: the first step owned by ``callee`` **within the forward
         suffix of the current workflow only** (issue #733) — the old
         first-match scan could return a step declared *before* the current
         position, re-entering a consumed step (the `stub_review` → `dev`
         loop). A mount-step ``current`` whose position could not be
         resolved to a *foreign* leaf skips the fallback entirely (review
         non-blocking item 5): silently degrading to a top-level agent-step
         scan could re-select another mount step owned by the callee and
         re-enter the wrong sub-workflow — failing empty (the run skips with
         a reason) is strictly safer.
    Empty string when the workflow cannot be loaded or owns no step for
    the callee.
    """
    try:
        result = resolve_next_from_workflow(wf_name, current_step, workflows_dir, local_workflows_dir=local_workflows_dir)
    except Exception:
        result = None
    if (
        result is not None
        and result.kind is RouteKind.ROUTE
        and result.task == callee
        and result.step
    ):
        return result.step
    workflow = _load_workflow_by_reference(wf_name, workflows_dir, local_workflows_dir)
    current = _find_step(workflow, current_step)
    if current is not None and _is_mount_step(current):
        position = resolve_position(
            workflow,
            current.name,
            resolve_ref=_mount_chain_ref_resolver(workflows_dir, local_workflows_dir),
            workflow_path=f"workflows/{wf_name}.yaml" if wf_name else "",
        )
        leaf = position.leaf()
        if leaf and leaf.workflow != wf_name and leaf.step and leaf.step != (exclude_step or ""):
            return leaf.step
        # The mount position degraded to a local (bare) position: the
        # foreign leaf is unknown. Do NOT fall through to the top-level
        # agent-step scan — re-entering via another mount step owned by the
        # callee would land in the wrong sub-workflow.
        print(
            f"Warning: mount step '{current_step}' in '{wf_name}' did not "
            f"resolve to a foreign leaf for callee '{callee}'; no step "
            f"assigned (silent fallback to a top-level step disabled)",
            file=sys.stderr,
        )
        return ""
    steps = list(getattr(workflow, "steps", None) or [])
    current_index = next(
        (i for i, step in enumerate(steps) if getattr(step, "name", "") == (current_step or "")),
        None,
    )
    if current_index is None:
        # No anchor in the workflow: there is no forward suffix, so a
        # cross-agent hand-off must fail empty rather than guess a step.
        return ""
    # Forward-only fallback (issue #733): a consumed step (at or before the
    # current position) must never be re-entered — the exact loop the old
    # first-match scan produced after `stub_review` handed off to `dev`.
    for step in steps[current_index + 1:]:
        if (
            getattr(step, "agent", None) == callee
            and getattr(step, "name", "")
            and step.name != (exclude_step or "")
        ):
            return step.name
    return ""


def _workflow_successor(
    wf_name: str,
    current_step: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> Optional[RouteResolution]:
    """Workflow-defined successor of ``current_step``, or None (issue #733)."""
    if not (wf_name and current_step):
        return None
    try:
        return resolve_next_from_workflow(
            wf_name, current_step, workflows_dir, local_workflows_dir=local_workflows_dir
        )
    except Exception:
        return None


def _is_human_gate_step(
    wf_name: str,
    step_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> bool:
    """True when ``step_name`` is declared ``human_gate: true`` (issue #733)."""
    if not (wf_name and step_name):
        return False
    try:
        workflow = _load_workflow_by_reference(wf_name, workflows_dir, local_workflows_dir)
    except Exception:
        return False
    step = _find_step(workflow, step_name)
    return bool(step is not None and getattr(step, "human_gate", False))


def _resolve_handoff_decision(
    prev_state: "WorkflowState",
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> HandoffDecision:
    """Classify a cross-agent ``function_call`` as an explicit decision (issue #733).

    Applies the two routing invariants in precedence order:

    1. **Gate precedence (security boundary).** A human gate outranks the
       agent-originated hand-off, with ``reason_code='gate_hold'`` so no call
       can route around it. Two shapes qualify: the *current* step is already
       parked on an unreleased ``human_gate`` hold (issue #887 round 2 — its
       own hold must not be handed off past), or the workflow successor of the
       current step is ``human_gate: true`` (issue #733).
    2. **Forward-only hand-off (correctness).** Otherwise the callee's step
       is resolved only from the forward suffix of the workflow (via
       :func:`_resolve_callee_step`); a consumed step is never re-entered.
       When the callee owns no forward step the hand-off is a no-op and the
       decision falls back to workflow continuation (``reason_code='dispatch'``).

    A call with no active workflow keeps its standalone hand-off semantics
    (``reason_code='function_call'`` with an empty target step).
    """
    function_call = getattr(prev_state, "function_call", None)
    callee = (getattr(function_call, "agent_name", "") or "").strip()
    if _is_human_gate_hold(getattr(prev_state, "turn_outcome", None)):
        # An armed root gate (issue #1003) makes the hand-off a *consultation*:
        # the gate blocks the workflow advance, never a delegation, so the
        # callee is routed and the gate stays armed at the unchanged position.
        # Without an armed gate the current step's own hold outranks the
        # hand-off: routing continues on the outcome path, which applies the
        # same guard and returns the hold (empty target — the gate releases to
        # a step, not to the callee).
        if _armed_gate(prev_state) is None:
            return HandoffDecision("", "", "gate_hold")
    wf_name = (getattr(prev_state, "workflow", "") or "") if prev_state is not None else ""
    if not wf_name:
        return HandoffDecision(callee, "", "function_call")
    current_step = (getattr(prev_state, "step", "") or "") if prev_state is not None else ""
    successor = _workflow_successor(wf_name, current_step, workflows_dir, local_workflows_dir)
    if successor and _is_human_gate_step(
        wf_name, successor.step, workflows_dir, local_workflows_dir
    ):
        return HandoffDecision(successor.task, successor.step, "gate_hold")
    target_step = _resolve_callee_step(
        wf_name, current_step, callee, workflows_dir, local_workflows_dir
    )
    if not target_step:
        # No forward step for the callee: never re-enter a consumed step, and
        # never carry a bare workflow context (an empty step would make the
        # result builder default back to the workflow entry step).
        return HandoffDecision("", "", "dispatch")
    return HandoffDecision(callee, target_step, "function_call")


# Bound on mount-chain descent (issue #618 review fix): a circular mount
# (A mounts B which mounts A) must terminate instead of recursing to
# RecursionError. Mirrors ``_MAX_SUB_WORKFLOW_DEPTH`` in resolve_workflow.py.
_MAX_POSITION_DEPTH = 10


# @stub-protected: signature defined by architect — do not modify
def resolve_position(
    wf_def: WorkflowDefinition,
    step_name: str,
    resolve_ref: Optional[Callable[[str], Optional[WorkflowDefinition]]] = None,
    workflow_path: str = "",
    *,
    _seen: Optional[set] = None,
    _depth: int = 0,
) -> "WorkflowPosition":
    """Build the flat :class:`WorkflowPosition` for a step (issue #638).

    Pure builder: walks the mount chain starting at ``wf_def`` /
    ``step_name`` and appends one :class:`StepRef` per level. When the
    step is a workflow-mount step (``workflow``/``path`` reference, no
    agent), the child definition is obtained via the ``resolve_ref`` port
    (the implement step wires it to ``_load_workflow_by_reference`` with
    ``workflows_dir`` / ``local_workflows_dir`` closure) and the walk
    appends the child's entry step. Stops at the first agent-bearing step
    (the leaf) or when the chain can no longer be resolved.

    Descent is bounded two ways (review fix): a seen-chain (definition
    identity) guard terminates circular mounts, and ``_depth`` caps
    non-cyclic chains at ``_MAX_POSITION_DEPTH`` levels. Both stop the
    walk at the current position rather than raising.

    For the #617 scenario this yields::

        refs = [
            StepRef(plan-orchestration, sub_impl),
            StepRef(tmp-impl, step_1),
            StepRef(dev-review-loop, implement),
        ]
    """
    def _bare(step_label: str = step_name) -> "WorkflowPosition":
        # Current position as the final ref — descent stops here (unknown
        # step, non-mount step, unresolvable ref, or a bound hit).
        return WorkflowPosition.bare(
            getattr(wf_def, "name", "") or step_label, step_label
        )

    step = _find_step(wf_def, step_name)
    if step is None:
        # Unknown step: record the requested name so the path stays
        # diagnostic, but do not descend (the leaf is this bare ref).
        return _bare()
    if not _is_mount_step(step):
        return _bare(step.name)
    # Descent bounds (review fix): a circular mount or an abnormally deep
    # non-cyclic chain stops at the current position instead of recursing.
    seen = _seen if _seen is not None else set()
    if id(wf_def) in seen or _depth >= _MAX_POSITION_DEPTH:
        return _bare(step.name)
    seen.add(id(wf_def))
    child_ref = step.workflow or step.path or ""
    child_def = resolve_ref(child_ref) if resolve_ref is not None else None
    if child_def is None:
        # Mount reference cannot be resolved (e.g. a dynamic tmp_impl
        # absent from the checkout): the position stops at the mount step.
        return _bare(step.name)
    entry_name = _entry_step_name(child_def)
    if not entry_name:
        return _bare(step.name)
    sub = resolve_position(
        child_def, entry_name, resolve_ref=resolve_ref,
        workflow_path=_child_workflow_path(child_def, child_ref),
        _seen=seen, _depth=_depth + 1,
    )
    # Prepend this level's ref: outermost first, the walk above already
    # holds the innermost-first tail (child entry ... leaf).
    this_ref = StepRef(
        workflow=getattr(wf_def, "name", "") or step_name, step=step.name,
    )
    return WorkflowPosition(refs=[this_ref, *sub.refs])


def _find_step(wf_def: Optional[WorkflowDefinition], step_name: str) -> Optional[WorkflowStep]:
    """Return the named step from a workflow definition, or None."""
    for step in getattr(wf_def, "steps", None) or []:
        if getattr(step, "name", "") == step_name:
            return step
    return None


def _is_mount_step(step: WorkflowStep) -> bool:
    """True for a workflow-mount step (workflow/path reference, no agent)."""
    return (
        not getattr(step, "agent", None)
        and bool(getattr(step, "workflow", None) or getattr(step, "path", None))
    )


def _entry_step_name(child_def: WorkflowDefinition) -> str:
    """Name of the mounted workflow's entry (first) step, or ''."""
    steps = getattr(child_def, "steps", None) or []
    return steps[0].name if steps else ""


def _child_workflow_path(child_def: WorkflowDefinition, child_ref: str) -> str:
    """Repo-root path of a loaded child workflow (issue #618, review fix).

    Prefers the loader-resolved file path recorded on the definition
    (``source_path``, set by the real ref port) over the old
    ``workflows/{name}.yaml`` name guess, which was wrong for
    consumer-local dirs and branch-mounted references. Falls back to the
    reference / name heuristic only when the loader did not record a path.
    """
    source_path = getattr(child_def, "source_path", None)
    if source_path:
        try:
            resolved = Path(source_path).resolve()
            repo_root = Path.cwd().resolve()
            if resolved.parent == repo_root:
                # workflows-dir sits directly at the repo root.
                return resolved.name
            return str(resolved.relative_to(repo_root.parent.resolve()))
        except (ValueError, OSError):
            pass
    name = getattr(child_def, "name", "") or ""
    if child_ref and ("/" in child_ref or child_ref.endswith((".yaml", ".yml"))):
        return child_ref
    return f"workflows/{name}.yaml" if name else child_ref


def _resolve_function_call_path(
    function_call: FunctionCall,
    workflows_dir: str,
    prev_state: Optional["WorkflowState"] = None,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve config for a stored imperative function call (call_agent).

    An explicit agent-to-agent hand-off pins the target agent and beats
    workflow continuation (same priority tier as a human mention). The
    call is one-shot: the fresh state built downstream clears
    ``function_call`` so a re-trigger no-ops.

    When a workflow is active (``prev_state.workflow`` set), the workflow
    context is carried through — ``config_workflow`` is the active workflow
    and ``config_step`` is the callee's step resolved from it (issue #507:
    returning empty severed the step context, so the callee posted a stale
    step and the following re-trigger dead-ended on the loop guard).
    Non-workflow calls keep the legacy empty behavior.

    Note: consumption is by supersession (the posted state block shadows
    older state-bearing comments). If this run's comment fails to post, the
    stale state still carries the call and a re-trigger would re-fire it —
    an accepted edge of the same idempotency model dispatch uses.
    """
    if function_call.function not in SUPPORTED_FUNCTIONS:
        # Registry already validated at parse time; guard against drift.
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Unsupported function_call: {function_call.function}",
        }

    task = (function_call.agent_name or "").strip()
    if not task:
        return {"SKIP_AGENT": "true", "REASON": "function_call call_agent has no agent_name"}

    complexity = function_call.complexity or ""
    wf_name = (getattr(prev_state, "workflow", "") or "") if prev_state is not None else ""
    # Establish the callee's step from the active workflow so the callee's
    # posted state records the step it actually ran (issue #507).
    wf_step = _resolve_callee_step(wf_name, getattr(prev_state, "step", "") or "", task, workflows_dir, local_workflows_dir) if wf_name else ""
    # Return address (issue #765): the explicit ``callback`` if the caller set
    # one, otherwise auto-default to the emitting agent so a callee that
    # finishes with no decision of its own routes back to whoever invoked it
    # (the agent-as-tools pattern). A callback equal to the callee is a no-op.
    emitter = (getattr(prev_state, "agent", "") or "").strip() if prev_state is not None else ""
    callback = (function_call.callback or "").strip() or emitter
    if callback == task:
        callback = ""
    print(
        f"Function call: calling agent '{task}' (complexity={complexity}, "
        f"workflow='{wf_name}', step='{wf_step}', callback='{callback}')",
        file=sys.stderr,
    )
    return {
        "task": task,
        "config_workflow": wf_name,
        "config_step": wf_step,
        "config_callback": callback,
        "config_complexity": complexity,
        "config_reasoning": "none",
        "config_temperature": "",
        "config_round": "",
        "config_model": "",
        "config_skills": "",
        "config_top_p": "",
        "config_top_k": "",
        "config_repetition_penalty": "",
        "config_debug": "",
        "config_branch": "",
        "config_parent_workflow": "",
        "config_parent_workflow_step": "",
    }


def _resolve_machine_trigger_path(
    trigger_body: str,
    workflows_dir: str,
) -> dict:
    """Resolve config for a CI-posted machine trigger token (issue #478).

    The generic ``bot-trigger: <agent>`` token replaces the legacy
    machine-posted ``@@architect`` comments now that @@mentions are blocked for
    bot-triggered runs. The target agent is pinned from the token; unknown
    agents skip with a reason.
    """
    task = extract_machine_trigger(trigger_body)
    if not task:
        return {"SKIP_AGENT": "true", "REASON": "No machine trigger token found"}
    if task not in TASK_TO_PROMPT:
        return {"SKIP_AGENT": "true", "REASON": f"Unknown machine trigger agent: {task}"}
    print(f"Machine trigger: invoking '{task}'", file=sys.stderr)
    return {
        "task": task,
        "config_workflow": "",
        "config_step": "",
        "config_complexity": "",
        "config_reasoning": "none",
        "config_temperature": "",
        "config_round": "",
        "config_model": "",
        "config_skills": "",
        "config_top_p": "",
        "config_top_k": "",
        "config_repetition_penalty": "",
        "config_debug": "",
        "config_branch": "",
        "config_parent_workflow": "",
        "config_parent_workflow_step": "",
    }


def _agent_step_entry(step: "WorkflowStep") -> dict:
    """Step-entry result for a plain ``agent`` step (issue #599)."""
    return {"task": step.agent, "skip_reason": None}


def _script_step_entry(step: "WorkflowStep") -> dict:
    """Step-entry result for a deterministic ``script`` step (issue #599).

    Carries the same ``run_script`` marker shape ``resolve_next_from_workflow``
    emits (issue #541) so ``build_result`` short-circuits to the script
    execution contract and the GHA pipeline runs the step's script directly.
    """
    return {
        "run_script": {
            "command": step.script.command,
            "args": step.script.to_process_env()["SCRIPT_ARGS"],
            "working_dir": step.script.working_dir or "",
            "env": step.script.to_process_env()["SCRIPT_ENV"],
            "timeout_minutes": step.script.timeout_minutes,
        },
        "skip_reason": None,
    }


def _sub_workflow_step_entry(
    step: "WorkflowStep",
    wf_def: "WorkflowDefinition",
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
    step_path: Optional[StepPath] = None,
) -> dict:
    """Step-entry result for a ``workflow``/``repo``/``path`` step (issue #599).

    Reuses the happy-path child-entry resolution (``resolve_sub_workflow_entry``)
    so the forced entry loads the child, resolves to its first step's agent,
    and sets the parent/return fields identically to normal execution.
    ``scaffold_branch`` enables fetch-on-miss for branch-mounted children that
    are absent from the local (default-branch) checkout (issue #604).
    ``step_path`` (issue #822) descends to a nested non-first step and carries
    the full emitted position through to the entry result.
    """
    entry = resolve_sub_workflow_entry(
        step, wf_def, workflows_dir, prompt_base, scaffold_branch,
        local_workflows_dir=local_workflows_dir, step_path=step_path,
    )
    if not entry or entry.get("SKIP_AGENT") == "true":
        reason = (entry or {}).get("REASON", f"step '{step.name}' is not a valid sub-workflow step")
        return {"skip_reason": f"force-next-step: {reason}"}
    return {
        "task": entry["ROLE_NAME"],
        "config_workflow": entry["WORKFLOW_NAME"],
        "config_step": entry["WORKFLOW_STEP"],
        "config_parent_workflow": entry["PARENT_WORKFLOW"],
        "config_parent_workflow_step": entry["PARENT_WORKFLOW_STEP"],
        "WORKFLOW_POSITION": entry.get("WORKFLOW_POSITION"),
        "WORKFLOW_POSITION_PATH": entry.get("WORKFLOW_POSITION_PATH", ""),
        "skip_reason": None,
    }


def _resolve_step_entry(
    step: "WorkflowStep",
    wf_def: "WorkflowDefinition",
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
    step_path: Optional[StepPath] = None,
) -> dict:
    """Resolve the dispatchable entry for a force-next-step target (issue #599).

    Shape-aware: a step has exactly one of ``agent``/``workflow``/``repo``/
    ``script`` (validated by the WorkflowStep schema). Returns a dict with
    ``task``/``run_script``/``config_*`` overrides when runnable, or
    ``skip_reason`` when the step cannot be force-run. ``step_path`` (issue
    #822) is the remaining nested descent for a dotted selector.
    """
    if step.agent:
        return _agent_step_entry(step)
    if step.script is not None:
        return _script_step_entry(step)
    if step.workflow or step.repo or step.path:
        return _sub_workflow_step_entry(
            step, wf_def, workflows_dir, prompt_base, scaffold_branch,
            local_workflows_dir, step_path,
        )
    return {
        "skip_reason": f"force-next-step: step '{step.name}' has no runnable source",
    }


def _build_force_next_result(
    entry: dict,
    config_step: str,
    config_workflow: str,
    fs_state: "WorkflowState",
) -> dict:
    """Build the force-next-step config dict from a step-entry result.

    Sub-workflow entries override the workflow/step and parent fields with the
    *child* workflow's first step and the explicit parent reference (matching
    the happy-path entry contract, so return-to-parent works); other shapes
    inherit the plain parent fields from the current state.
    """
    return {
        "task": entry.get("task", ""),
        "run_script": entry.get("run_script"),
        "config_step": entry.get("config_step") or config_step,
        "config_workflow": entry.get("config_workflow") or config_workflow,
        "config_complexity": "",
        "config_reasoning": "none",
        "config_temperature": "",
        "config_round": "",
        "config_model": "",
        "config_skills": "",
        "config_top_p": "",
        "config_top_k": "",
        "config_repetition_penalty": "",
        "config_debug": "",
        "config_branch": "",
        "config_parent_workflow": (
            entry.get("config_parent_workflow")
            or (fs_state.parent_workflow or "")
        ),
        "config_parent_workflow_step": (
            entry.get("config_parent_workflow_step")
            or (fs_state.parent_workflow_step or "")
        ),
        # Nested entry emits its full position (issue #822); a plain entry
        # leaves it empty so ``_attach_position`` rebuilds the chain.
        "config_position_json": entry.get("WORKFLOW_POSITION") or "",
        "config_position_path": entry.get("WORKFLOW_POSITION_PATH") or "",
    }


def _resolve_human_path(
    trigger_body: str,
    repo: Optional[str],
    issue_number: Optional[int],
    workflows_dir: str,
    prev_state: Optional["WorkflowState"] = None,
    pinned_task: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
    prompt_base: str = "",
    scaffold_branch: Optional[str] = None,
    workflow_command: Optional[WorkflowCommand] = None,
) -> dict:
    """Resolve config for a human-triggered comment.

    Handles /nebuko force-step, /nebuko run, and @@mentions.
    Falls back to fetching workflow state from comment history when needed.

    Args:
        trigger_body: Raw trigger body text.
        repo: Repository in owner/name format.
        issue_number: Issue/PR number.
        workflows_dir: Path to workflows directory.
        prev_state: Pre-fetched workflow state (from comment history).
            If None or without workflow, will attempt to fetch from history.
        pinned_task: Agent name pinned by the caller for CI plain-mention
            triggers (ci_mention). These bodies carry a bot marker, so
            ``extract_mention_agent`` refuses to yield one for them (issue
            #478); the caller supplies the mention explicitly instead.
        local_workflows_dir: Optional consumer-local workflows dir that takes
            precedence over ``workflows_dir`` (issue #529).
        prompt_base: Path to prompt files; needed by the force-next-step
            branch to resolve sub-workflow targets (issue #599).
        scaffold_branch: Branch carrying a branch-mounted child workflow;
            enables fetch-on-miss in the force-next-step sub-workflow entry
            (issue #604). Defaults to the state's own value when not given.

    Returns:
        Config dict with task and config_* variables, or SKIP_AGENT dict.
    """
    # /nebuko force-step step --name <step> (human only). The flag grammar
    # makes --name required, so the legacy bare-command auto-advance arm is
    # gone (issue #635).
    wf_cmd = workflow_command
    target_step = (
        wf_cmd.params["name"]
        if wf_cmd is not None and wf_cmd.action == "force-step"
        else None
    )
    if target_step:
        # Use pre-fetched state, fall back to body, then to comment history
        fs_state = prev_state
        if not fs_state:
            fs_state = extract_state_from_body(trigger_body)
        if not fs_state or not fs_state.workflow:
            fs_state = _fetch_workflow_state_from_history(repo, issue_number)
        if not fs_state or not fs_state.workflow:
            return {
                "SKIP_AGENT": "true",
                "REASON": "force-step requires an active workflow (no workflow state found)",
            }
        # Branch-mounted children (issue #604): fall back to the state's own
        # scaffold_branch when the caller did not pass one.
        if scaffold_branch is None:
            scaffold_branch = fs_state.scaffold_branch
        config_workflow = fs_state.workflow
        if not _SAFE_WF_NAME_RE.match(config_workflow):
            return {
                "SKIP_AGENT": "true",
                "REASON": f"force-step: invalid workflow name '{config_workflow}'",
            }
        try:
            wf_file_force = load_workflow_with_dirs(
                config_workflow, workflows_dir, local_workflows_dir
            )
            wf_def = load_workflow(str(wf_file_force))
        except (FileNotFoundError, ValueError) as e:
            return {
                "SKIP_AGENT": "true",
                "REASON": f"force-step: workflow '{config_workflow}' not found ({e})",
            }

        path = StepPath.parse(target_step)
        if path is None:
            return {
                "SKIP_AGENT": "true",
                "REASON": f"force-step: invalid target step name '{target_step}'",
            }
        # Explicit target step — resolve its shape-aware entry. A dotted
        # selector (issue #822) names a mount step whose remaining segments
        # descend into the mounted child.
        target_step_def = next(
            (s for s in wf_def.steps if s.name == path.segments[0]), None
        )
        if target_step_def is None:
            return {
                "SKIP_AGENT": "true",
                "REASON": f"force-step: step '{target_step}' not found in workflow '{config_workflow}'",
            }
        if path.is_nested and not _is_mount_step(target_step_def):
            return {
                "SKIP_AGENT": "true",
                "REASON": (
                    f"force-step: step '{path.segments[0]}' is not a "
                    f"sub-workflow mount; cannot descend '{target_step}'"
                ),
            }
        tail = path.model_copy(update={"segments": path.segments[1:]}) if path.is_nested else None
        entry = _resolve_step_entry(
            target_step_def, wf_def, workflows_dir, prompt_base,
            scaffold_branch, local_workflows_dir, step_path=tail,
        )
        if entry.get("skip_reason"):
            return {"SKIP_AGENT": "true", "REASON": entry["skip_reason"]}
        return _build_force_next_result(
            entry, config_step=target_step,
            config_workflow=config_workflow, fs_state=fs_state,
        )

    # /nebuko run workflow --name <name> [--step <step>] (human only)
    wf_run = wf_cmd if (wf_cmd is not None and wf_cmd.action == "run") else None
    wf_name = wf_run.params.get("name") if wf_run else None
    requested_step = (wf_run.params.get("step") or "") if wf_run else ""
    wf_resolved = False
    task = ""
    wf_fail_reason = ""

    entry_point: Optional[WorkflowEntryPoint] = None
    if wf_name and _SAFE_WF_NAME_RE.match(wf_name):
        try:
            wf_file = load_workflow_with_dirs(
                wf_name, workflows_dir, local_workflows_dir
            )
            wf_def = load_workflow(str(wf_file))
            task = wf_def.entry_agent
            # A workflow is attemptable whenever it has steps (issue #816): the
            # first step is the entry, and a first-step sub-workflow mount is
            # descended to its runnable leaf below. Only a step-less workflow
            # (entry agent "", nothing to start) falls through to @@mention.
            wf_resolved = bool(wf_def.steps)
            # Issue #789: enter at the requested step (``--step``; default the
            # first step). Issue #609: this also descends a first-step
            # sub-workflow mount (e.g. the scaffold's ``tmp-impl``) to its
            # innermost runnable leaf instead of starting on the mount, and
            # carries the parent/return fields. Issue #822: a dotted selector
            # (``sub_impl.step_3``) descends into the mounted child's steps.
            if wf_resolved and requested_step and StepPath.parse(requested_step) is None:
                wf_resolved = False
                wf_fail_reason = (
                    f"/nebuko '{wf_name}': invalid step name '{requested_step}'"
                )
                task = ""
            elif wf_resolved:
                entry_point = WorkflowEntryPoint(
                    workflow=wf_name, step=requested_step
                ).resolve(
                    wf_def,
                    lambda target, tail=None: _resolve_step_entry(
                        target, wf_def, workflows_dir, prompt_base,
                        scaffold_branch, local_workflows_dir, step_path=tail,
                    ),
                )
                if not entry_point.is_valid():
                    wf_resolved = False
                    wf_fail_reason = entry_point.skip_reason
                    task = ""
                else:
                    task = entry_point.task
        except FileNotFoundError as e:
            # Multi-dir miss: the exception message enumerates every path
            # searched (issue #529).
            print(f"Warning: {e}, falling back to @@mention", file=sys.stderr)
            wf_fail_reason = f"/nebuko '{wf_name}': {e}"
        except ValueError as e:
            # A schema-invalid file (local override or built-in) still wins —
            # fail loudly instead of silently falling back to another file of
            # the same name (issue #529).
            print(
                f"Warning: workflow '{wf_name}' is schema-invalid ({e}); "
                f"no silent fallback to another directory",
                file=sys.stderr,
            )
            wf_fail_reason = f"/nebuko '{wf_name}': schema-invalid ({e})"

    if not wf_resolved:
        # Fall through to @@mention if /nebuko was invalid/unresolvable.
        # pinned_task covers CI plain-mention triggers whose bot marker would
        # otherwise make extract_mention_agent return None (issue #478).
        task = pinned_task or extract_mention_agent(trigger_body) or ""
        wf_name = None
        entry_point = None

    if entry_point is not None:
        # The entry point owns the descent overrides (issue #609): the active
        # workflow/step is the innermost leaf, not the one named in the trigger.
        config_workflow = entry_point.config_workflow
        config_step = entry_point.config_step
        config_parent_workflow = entry_point.config_parent_workflow or ""
        config_parent_workflow_step = entry_point.config_parent_workflow_step or ""
        run_script = entry_point.run_script
        config_position_json = entry_point.position_json or ""
        config_position_path = entry_point.position_path or ""
    else:
        config_workflow = wf_name if wf_name and wf_resolved else ""
        config_step = ""
        config_parent_workflow = ""
        config_parent_workflow_step = ""
        run_script = None
        config_position_json = ""
        config_position_path = ""
    result = {
        "task": task,
        "run_script": run_script,
        "config_workflow": config_workflow,
        "config_step": config_step,
        "config_complexity": "",
        "config_reasoning": "none",
        "config_temperature": "",
        "config_round": "",
        "config_model": "",
        "config_skills": "",
        "config_top_p": "",
        "config_top_k": "",
        "config_repetition_penalty": "",
        "config_debug": extract_debug_flag(trigger_body),
        "config_branch": "",
        "config_parent_workflow": config_parent_workflow,
        "config_parent_workflow_step": config_parent_workflow_step,
        "config_position_json": config_position_json,
        "config_position_path": config_position_path,
    }
    # Diagnostic for the terminal skip comment when /nebuko failed and no
    # mention fallback produced a task (issue #529).
    if wf_fail_reason and not task:
        result["config_skip_reason"] = wf_fail_reason
    return result


def _resolve_bot_fallback_path(
    trigger_body: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve config for a bot comment that carries state but no outcome.

    A bot comment's answer is routed from the ``turn_outcome`` its state block
    records (issue #964); a state with no outcome on record cannot be routed
    and skips with a diagnostic reason.

    Args:
        trigger_body: Raw trigger body text.
        workflows_dir: Path to workflows directory.
        local_workflows_dir: Optional consumer-local workflows dir (issue #529).

    Returns:
        Config dict with task and config_* variables, or SKIP_AGENT dict.
    """
    prev_state = extract_state_from_body(trigger_body)
    outcome = extract_turn_outcome_from_state(trigger_body, prev_state)
    if outcome is None:
        return {
            "SKIP_AGENT": "true",
            "REASON": "Bot comment without a recorded turn outcome",
        }

    # Human-gate hold precedence (issue #887): the state-only bot path never
    # reads the workflow forward edge, but the same guard keeps the behavior
    # uniform with `_resolve_outcome_path` and names the release commands.
    # A pending agent-to-agent hand-off is routed first (issue #1003).
    if _is_human_gate_hold(outcome):
        gate = _armed_gate(prev_state)
        if gate is not None:
            consult = _resolve_gate_consult(
                prev_state, gate, workflows_dir, local_workflows_dir
            )
            if consult is not None:
                return consult
            return _human_gate_hold_result(
                getattr(prev_state, "step", "") or "",
                _consult_exhausted_note(prev_state),
            )
        return _human_gate_hold_result(getattr(prev_state, "step", "") or "")

    if prev_state is None:
        return {
            "SKIP_AGENT": "true",
            "REASON": "Bot comment without workflow state",
        }

    config = _route_config_from_prefs(prev_state, prev_state.prefs)
    agent_hint = (outcome.required_agent or "").strip()

    if outcome.appreciation == Appreciation.HOLD:
        return _hold_skip_result(outcome, prev_state)

    if outcome.appreciation is not None or agent_hint:
        # A verdict (or an explicit hand-off target) routes the run.
        config["task"] = agent_hint
        if not config["task"]:
            wf_result = resolve_next_from_workflow(
                config["config_workflow"],
                config["config_step"],
                workflows_dir,
                branch_result=outcome.route_key,
                local_workflows_dir=local_workflows_dir,
            )
            if wf_result is not None and wf_result.skip_agent:
                # Surfaced mount-resolution failure (issue #671).
                return wf_result.skip_result()
            _apply_route_resolution(config, wf_result, prev_state)
            if not config["task"] and not config.get("run_script"):
                return _unmatched_verdict_result(outcome, prev_state, workflows_dir)
        return config

    # No verdict: follow the linear/default workflow edge.
    wf_result = resolve_next_from_workflow(
        config["config_workflow"],
        config["config_step"],
        workflows_dir,
        local_workflows_dir=local_workflows_dir,
    )
    if wf_result is not None and wf_result.skip_agent:
        return wf_result.skip_result()
    _apply_route_resolution(config, wf_result, prev_state)
    if config["task"] or config.get("run_script"):
        return config
    terminal = _terminal_skip_result(
        prev_state, workflows_dir, local_workflows_dir, wf_result
    )
    if terminal is not None:
        return terminal
    return _awaiting_input_result(prev_state, outcome)


def _recover_current_step(prev_state: "WorkflowState") -> str:
    """Recover the current step from a state whose scalar step may be empty.

    Fallback chain (issue #458), in priority order:
    1. ``prev_state.step`` — authoritative when set;
    2. last non-empty history entry's ``step`` — the most recently completed
       step, the freshest truth for legacy/corrupt states;
    3. ``resolution.selected_step`` — last resort only: it denotes the step
       the *previous* run resolved to, so it can be stale relative to history.

    The stored ``resolution.next_step`` is deliberately NOT used: it denotes
    the *next* step, so consulting it would skip a step.
    """
    if prev_state.step:
        return prev_state.step
    for entry in reversed(prev_state.history):
        if entry.step:
            return entry.step
    if prev_state.resolution and prev_state.resolution.selected_step:
        return prev_state.resolution.selected_step
    return ""


def _resolve_workflow_next_step(
    prev_state: "WorkflowState",
    outcome_obj: Optional[TurnOutcome],
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve next step from workflow YAML when state carries no outcome yet.

    Uses prev_state.workflow + prev_state.step to look up the next agent
    in the workflow definition. When the scalar step is empty (legacy/corrupt
    states), falls back to the state's history (issue #458).

    Args:
        prev_state: Workflow state containing workflow name and current step.
        outcome_obj: The state's turn outcome, if any (it supplies only the
            callback precedence, never a route key on this path).
        workflows_dir: Path to workflows directory.

    Returns:
        Config dict with resolved task and config_* variables.
    """
    wf_name = prev_state.workflow
    current_step = _recover_current_step(prev_state)

    if not _SAFE_WF_NAME_RE.match(wf_name):
        return {"SKIP_AGENT": "true", "REASON": f"Invalid workflow name '{wf_name}'"}

    config = {
        "task": "",
        "config_complexity": "",
        "config_reasoning": "none",
        "config_temperature": "",
        "config_round": "",
        "config_model": "",
        "config_skills": "",
        "config_top_p": "",
        "config_top_k": "",
        "config_repetition_penalty": "",
        "config_debug": "",
        "config_workflow": wf_name,
        "config_step": current_step or "",
        "config_branch": "",
        "config_parent_workflow": "",
        "config_parent_workflow_step": "",
    }

    # Callback return (issue #765): same advance-only fallback as the
    # outcome-driven path, for callees whose state carries a caller callback.
    callback_target = _callback_return_target(
        prev_state, outcome_obj, workflows_dir, local_workflows_dir
    )
    if callback_target is not None:
        target_agent, target_step = callback_target
        config["task"] = target_agent
        config["config_step"] = target_step
        print(
            f"Callback: returning to agent '{target_agent}' "
            f"(workflow='{wf_name}', step='{target_step}')",
            file=sys.stderr,
        )
        return config

    # Resolve next step from workflow YAML
    wf_result = resolve_next_from_workflow(
        wf_name,
        current_step,
        workflows_dir,
        agent_hint=None,
        branch_result=None,
        parent_workflow=prev_state.parent_workflow or "",
        parent_workflow_step=prev_state.parent_workflow_step or "",
        local_workflows_dir=local_workflows_dir,
        scaffold_branch=prev_state.scaffold_branch,
        history=prev_state.history,
    )
    if wf_result is not None and wf_result.skip_agent:
        # Surfaced mount-resolution failure (issue #671).
        return wf_result.skip_result()
    if wf_result is not None and wf_result.kind not in (
        RouteKind.DECLARED_FINISH,
        RouteKind.UNRESOLVED,
    ):
        _apply_route_resolution(config, wf_result, prev_state)
    if config["task"] or config.get("run_script"):
        return config
    # Nothing resolved to a successor: disambiguate the skip reason (issue #458)
    # so a real config failure never reads as a benign end-of-workflow.
    return _workflow_next_step_skip_reason(wf_name, current_step, workflows_dir)


def _workflow_next_step_skip_reason(
    wf_name: str, current_step: str, workflows_dir: str
) -> dict:
    """Disambiguate SKIP reasons when no successor was found (issue #458).

    Distinguishes a genuine terminal step (the step exists in the workflow but
    has no ``next``) from resolution failures (unknown step name, missing/
    unreadable workflow file, or an undeterminable current step), so real
    failures are diagnosable instead of hiding behind a generic message.
    """
    if not current_step:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Resolution failed: could not determine current step (step='{current_step}')",
        }

    try:
        workflow = load_workflow(str(Path(workflows_dir) / f"{wf_name}.yaml"))
    except FileNotFoundError:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Resolution failed: workflow file '{wf_name}.yaml' not found",
        }
    except Exception as exc:  # malformed YAML / validation errors
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Resolution failed: could not load workflow '{wf_name}' ({exc.__class__.__name__})",
        }

    step_def = next(
        (s for s in workflow.steps if s.name == current_step), None
    )
    if step_def is None:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Resolution failed: step '{current_step}' not found in workflow '{wf_name}'",
        }
    if _is_terminal_step(step_def):
        # The step exists and has no successor: a benign end-of-workflow stop.
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Terminal step '{current_step}' has no next in workflow '{wf_name}'",
            "SKIP_KIND": "terminal",
        }
    # Step has a `next` (or conditions) but no successor resolved —
    # e.g. target step missing from the workflow definition.
    return {
        "SKIP_AGENT": "true",
        "REASON": f"Resolution failed: step '{current_step}' in workflow '{wf_name}' has a next reference that does not resolve",
    }

