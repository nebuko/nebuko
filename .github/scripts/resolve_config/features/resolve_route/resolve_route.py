"""Routing feature.

The routing policy: given parsed state + trigger commands, decide which
path the next agent takes and produce the resolved config dict.
"""

import re
import sys
from pathlib import Path
from typing import Any, Optional

from config import (
    WorkflowDefinition,
    WorkflowStep,
    load_workflow,
    load_workflow_with_dirs,
)
from resolve_workflow import (
    resolve_next_from_workflow,
    resolve_sub_workflow_entry,
    _is_terminal_step,
    _load_workflow_by_reference,
    TASK_TO_PROMPT,
)
from validate_output import has_agent_run_config_marker
from workflow_state import (
    FunctionCall,
    SUPPORTED_FUNCTIONS,
    extract_dispatch_from_state,
    extract_state_from_body,
)

from ..fetch_state.fetch_state import _fetch_workflow_state_from_history
from ..parse_trigger.parse_trigger import (
    extract_force_next_command,
    extract_mention_agent,
    extract_resume_command,
    extract_workflow_command,
    extract_debug_flag,
    extract_machine_trigger,
    normalize_resume_input,
)

__all__ = [
    "MAX_LOOP_COUNT",
    "resolve_next_agent_from_workflow",
    "count_consecutive_same_agent",
    "_resolve_function_call_path",
    "_resolve_machine_trigger_path",
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
) -> dict:
    """Resolve config to re-run the currently held step after /workflow resume.

    Validates that dispatch.status == "hold", looks up the current step in the
    workflow YAML, then builds a config for the same step/agent (replaying so the
    agent can process the human's response). Copies dispatch parameters from the
    previously stored dispatch. Injects human input into current_context so the
    agent prompt can reference it.

    Args:
        prev_state: WorkflowState containing the held workflow/step/agent.
        trigger_body: Raw trigger body containing the /workflow resume command.
        workflows_dir: Path to workflows directory.
        local_workflows_dir: Optional consumer-local dir with precedence
            over ``workflows_dir`` (issue #529).

    Returns:
        Config dict for re-running the held step, or SKIP_AGENT dict.
    """
    dispatch = prev_state.dispatch

    # Validate that this step is actually on hold
    if dispatch is None or dispatch.status != "hold":
        return {
            "SKIP_AGENT": "true",
            "REASON": f"resume: step is not on hold (dispatch.status='{dispatch.status if dispatch else 'none'}')",
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

    # Extract human input text from the resume command. A bare resume
    # (no feedback text) maps to the BARE_RESUME_INPUT sentinel so the
    # human_gate interceptor (issue #506) recognizes this run as
    # human-triggered and lets the re-run's advance pass the gate.
    human_input = normalize_resume_input(extract_resume_command(trigger_body))

    # Copy dispatch parameters from previous state so the step re-runs
    # with the same LLM settings (model, temperature, etc.)
    config = {
        "task": step_def.agent,
        "config_complexity": dispatch.complexity or "medium",
        "config_reasoning": dispatch.reasoning or "none",
        "config_temperature": (
            str(dispatch.temperature) if dispatch.temperature is not None else ""
        ),
        "config_round": "",
        "config_model": dispatch.model or "",
        "config_skills": dispatch.skills or "",
        "config_top_p": (
            str(dispatch.top_p) if dispatch.top_p is not None else ""
        ),
        "config_top_k": (
            str(dispatch.top_k) if dispatch.top_k is not None else ""
        ),
        "config_repetition_penalty": (
            str(dispatch.repetition_penalty) if dispatch.repetition_penalty is not None else ""
        ),
        "config_debug": str(dispatch.debug).lower() if dispatch.debug else "",
        "config_workflow": wf_name,
        "config_step": current_step_in_wf,
        "config_branch": dispatch.branch or "",
        "config_parent_workflow": dispatch.parent_workflow or "",
        "config_parent_workflow_step": dispatch.parent_workflow_step or "",
        "config_human_resume_input": human_input,
    }

    return config


def _apply_wf_marker(
    config: dict,
    wf_result: dict,
) -> None:
    """Apply a sub-workflow transition marker to the resolved config.

    Entry: switch ``config_workflow``/``config_step`` to the sub-workflow's
    first step and record the parent reference for the return leg.
    Exit: restore the parent workflow, resume at the step after the
    sub-workflow reference, and clear the parent tracking fields.
    """
    if wf_result.get("entered_sub_workflow"):
        config["config_workflow"] = wf_result["workflow"]
        config["config_step"] = wf_result["step"]
        config["config_parent_workflow"] = wf_result["parent_workflow"]
        config["config_parent_workflow_step"] = wf_result["parent_workflow_step"]
        return

    if wf_result.get("exited_sub_workflow"):
        config["config_workflow"] = wf_result["workflow"]
        config["config_step"] = wf_result["step"]
        config["config_parent_workflow"] = ""
        config["config_parent_workflow_step"] = ""
        if wf_result.get("branch"):
            config["config_branch"] = wf_result["branch"]


def _resolve_dispatch_path(
    prev_state: "WorkflowState",
    dispatch_obj: "Dispatch",
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve config when a dispatch object is present in the trigger body.

    Handles dispatch statuses (advance, loop, hold, terminate) and
    workflow-driven step progression.

    Args:
        prev_state: WorkflowState from the trigger body.
        dispatch_obj: Dispatch object extracted from prev_state.
        workflows_dir: Path to workflows directory.
        local_workflows_dir: Optional consumer-local workflows dir that takes
            precedence over ``workflows_dir`` (issue #529).

    Returns:
        Config dict with task and all config_* variables, or SKIP_AGENT dict.
    """
    agent_hint = dispatch_obj.task_hint or ""
    branch_result = dispatch_obj.branch

    config = {
        "task": "",
        "config_complexity": dispatch_obj.complexity or "medium",
        "config_reasoning": dispatch_obj.reasoning or "none",
        "config_temperature": str(dispatch_obj.temperature) if dispatch_obj.temperature is not None else "",
        "config_round": "",
        "config_model": dispatch_obj.model or "",
        "config_skills": dispatch_obj.skills or "",
        "config_top_p": str(dispatch_obj.top_p) if dispatch_obj.top_p is not None else "",
        "config_top_k": str(dispatch_obj.top_k) if dispatch_obj.top_k is not None else "",
        "config_repetition_penalty": str(dispatch_obj.repetition_penalty) if dispatch_obj.repetition_penalty is not None else "",
        "config_debug": str(dispatch_obj.debug).lower() if dispatch_obj.debug else "",
        "config_workflow": dispatch_obj.workflow or "",
        "config_step": dispatch_obj.step or "",
        "config_branch": dispatch_obj.branch or "",
        "config_parent_workflow": dispatch_obj.parent_workflow or "",
        "config_parent_workflow_step": dispatch_obj.parent_workflow_step or "",
    }

    # Workflow-driven resolution using previous workflow state
    if prev_state and prev_state.workflow and prev_state.step:
        parent_wf = dispatch_obj.parent_workflow or prev_state.parent_workflow or ""
        parent_wf_step = (
            dispatch_obj.parent_workflow_step or prev_state.parent_workflow_step or ""
        )
        wf_result = resolve_next_from_workflow(
            prev_state.workflow,
            prev_state.step,
            workflows_dir,
            agent_hint=None,
            branch_result=branch_result,
            parent_workflow=parent_wf,
            parent_workflow_step=parent_wf_step,
            local_workflows_dir=local_workflows_dir,
        )
        if wf_result:
            if wf_result.get("run_script"):
                # Deterministic script successor (issue #541): carry the script
                # payload instead of an agent task; build_result short-circuits.
                config["run_script"] = {
                    "path": wf_result.get("script_path", ""),
                    "args_str": wf_result.get("script_args_str", ""),
                    "timeout_minutes": wf_result.get("timeout_minutes"),
                }
                config["config_step"] = wf_result.get("step", config["config_step"])
                config["config_workflow"] = prev_state.workflow
            else:
                config["task"] = wf_result["task"]
                config["config_step"] = wf_result.get("step", config["config_step"])
                if wf_result.get("entered_sub_workflow") or wf_result.get("exited_sub_workflow"):
                    _apply_wf_marker(config, wf_result)
                else:
                    config["config_workflow"] = prev_state.workflow
        else:
            dispatch_status = dispatch_obj.status or ""
            if dispatch_status == "hold":
                return {"SKIP_AGENT": "true", "REASON": "Dispatch status: hold"}
            elif dispatch_status == "terminate":
                return {"SKIP_AGENT": "true", "REASON": "Dispatch status: terminate"}
            elif dispatch_status == "loop":
                config["task"] = prev_state.agent or ""
            elif dispatch_status == "advance":
                config["task"] = agent_hint
            else:
                return {
                    "SKIP_AGENT": "true",
                    "REASON": "Terminal step reached",
                }
    else:
        config["task"] = agent_hint

    return config


def _resolve_callee_step(wf_name: str, current_step: str, callee: str, workflows_dir: str, local_workflows_dir: Optional[str] = None) -> str:
    """Resolve the workflow step that ``callee`` should run (issue #507).

    When a caller hands off via ``function_call`` while a workflow is active,
    the callee's actual workflow step must be established — otherwise the
    callee's posted state inherits the *caller's* stale step and the next
    re-trigger mis-resolves the next task to the callee itself, which the
    same-agent loop guard then skips (issue #506 repro).

    Chain: the linear successor of ``current_step`` whose ``agent == callee``
    (matches in-workflow hand-offs such as genius(analyze) -> ctx(context));
    failing that, the first workflow step owned by ``callee``. Empty string
    when the workflow cannot be loaded or owns no step for the callee.
    """
    try:
        result = resolve_next_from_workflow(wf_name, current_step, workflows_dir, local_workflows_dir=local_workflows_dir)
    except Exception:
        return ""
    if result and result.get("task") == callee and result.get("step"):
        return result["step"]
    workflow = _load_workflow_by_reference(wf_name, workflows_dir, local_workflows_dir)
    for step in getattr(workflow, "steps", None) or []:
        if getattr(step, "agent", None) == callee and getattr(step, "name", ""):
            return step.name
    return ""


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

    complexity = function_call.complexity or "medium"
    wf_name = (getattr(prev_state, "workflow", "") or "") if prev_state is not None else ""
    # Establish the callee's step from the active workflow so the callee's
    # posted state records the step it actually ran (issue #507).
    wf_step = _resolve_callee_step(wf_name, getattr(prev_state, "step", "") or "", task, workflows_dir, local_workflows_dir) if wf_name else ""
    print(
        f"Function call: calling agent '{task}' (complexity={complexity}, "
        f"workflow='{wf_name}', step='{wf_step}')",
        file=sys.stderr,
    )
    return {
        "task": task,
        "config_workflow": wf_name,
        "config_step": wf_step,
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

    Machine tokens (``bot-subtask-complete``, ``bot-summarizer-trigger``,
    generic ``bot-trigger: <agent>``) replace the legacy machine-posted
    ``@@genius`` / ``@@summarize`` comments now that @@mentions are blocked
    for bot-triggered runs. The target agent is pinned from the token;
    unknown agents skip with a reason.
    """
    machine = extract_machine_trigger(trigger_body)
    if not machine:
        return {"SKIP_AGENT": "true", "REASON": "No machine trigger token found"}
    task, extra = machine
    if task not in TASK_TO_PROMPT:
        return {"SKIP_AGENT": "true", "REASON": f"Unknown machine trigger agent: {task}"}
    label = f"Machine trigger: invoking '{task}'" + (f" (subtask {extra})" if extra else "")
    print(label, file=sys.stderr)
    return {
        "task": task,
        "config_workflow": "",
        "config_step": "",
        "config_complexity": "medium",
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
            "path": step.script.path,
            "args_str": " ".join(step.script.args or []),
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
) -> dict:
    """Step-entry result for a ``workflow``/``repo``/``path`` step (issue #599).

    Reuses the happy-path child-entry resolution (``resolve_sub_workflow_entry``)
    so the forced entry loads the child, resolves to its first step's agent,
    and sets the parent/return fields identically to normal execution.
    ``scaffold_branch`` enables fetch-on-miss for branch-mounted children that
    are absent from the local (default-branch) checkout (issue #604).
    """
    entry = resolve_sub_workflow_entry(
        step, wf_def, workflows_dir, prompt_base, scaffold_branch
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
        "skip_reason": None,
    }


def _resolve_step_entry(
    step: "WorkflowStep",
    wf_def: "WorkflowDefinition",
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
) -> dict:
    """Resolve the dispatchable entry for a force-next-step target (issue #599).

    Shape-aware: a step has exactly one of ``agent``/``workflow``/``repo``/
    ``script`` (validated by the WorkflowStep schema). Returns a dict with
    ``task``/``run_script``/``config_*`` overrides when runnable, or
    ``skip_reason`` when the step cannot be force-run.
    """
    if step.agent:
        return _agent_step_entry(step)
    if step.script is not None:
        return _script_step_entry(step)
    if step.workflow or step.repo or step.path:
        return _sub_workflow_step_entry(
            step, wf_def, workflows_dir, prompt_base, scaffold_branch
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
        "config_complexity": "medium",
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
) -> dict:
    """Resolve config for a human-triggered comment.

    Handles force-next-step commands, /workflow commands, and @@mentions.
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
    # Check for force-next-step command first (human only)
    force_next = extract_force_next_command(trigger_body)
    if force_next:
        _, target_step = force_next
        # Use pre-fetched state, fall back to body, then to comment history
        fs_state = prev_state
        if not fs_state:
            fs_state = extract_state_from_body(trigger_body)
        if not fs_state or not fs_state.workflow:
            fs_state = _fetch_workflow_state_from_history(repo, issue_number)
        if not fs_state or not fs_state.workflow:
            return {
                "SKIP_AGENT": "true",
                "REASON": "force-next-step requires an active workflow (no workflow state found)",
            }
        # Branch-mounted children (issue #604): fall back to the state's own
        # scaffold_branch when the caller did not pass one.
        if scaffold_branch is None:
            scaffold_branch = fs_state.scaffold_branch
        config_workflow = fs_state.workflow
        if not _SAFE_WF_NAME_RE.match(config_workflow):
            return {
                "SKIP_AGENT": "true",
                "REASON": f"force-next-step: invalid workflow name '{config_workflow}'",
            }
        current_step_in_wf = fs_state.step
        wf_file_force = Path(workflows_dir) / f"{config_workflow}.yaml"
        try:
            wf_file_force = load_workflow_with_dirs(
                config_workflow, workflows_dir, local_workflows_dir
            )
            wf_def = load_workflow(str(wf_file_force))
        except (FileNotFoundError, ValueError) as e:
            return {
                "SKIP_AGENT": "true",
                "REASON": f"force-next-step: workflow '{config_workflow}' not found ({e})",
            }

        if target_step:
            if not _SAFE_WF_NAME_RE.match(target_step):
                return {
                    "SKIP_AGENT": "true",
                    "REASON": f"force-next-step: invalid target step name '{target_step}'",
                }
            # Explicit target step — resolve its shape-aware entry
            target_step_def = None
            for step in wf_def.steps:
                if step.name == target_step:
                    target_step_def = step
                    break
            if target_step_def is None:
                return {
                    "SKIP_AGENT": "true",
                    "REASON": f"force-next-step: step '{target_step}' not found in workflow '{config_workflow}'",
                }
            entry = _resolve_step_entry(
                target_step_def, wf_def, workflows_dir, prompt_base,
                scaffold_branch,
            )
            if entry.get("skip_reason"):
                return {"SKIP_AGENT": "true", "REASON": entry["skip_reason"]}
            return _build_force_next_result(
                entry, config_step=target_step,
                config_workflow=config_workflow, fs_state=fs_state,
            )
        elif current_step_in_wf:
            # Auto-advance: find current step and get its .next (skip conditions)
            current_step_def = None
            for step in wf_def.steps:
                if step.name == current_step_in_wf:
                    current_step_def = step
                    break
            if current_step_def is None:
                return {
                    "SKIP_AGENT": "true",
                    "REASON": f"force-next-step: current step '{current_step_in_wf}' not found in workflow '{config_workflow}'",
                }
            if not current_step_def.next:
                return {
                    "SKIP_AGENT": "true",
                    "REASON": f"force-next-step: current step '{current_step_in_wf}' is terminal (no next step)",
                }
            next_step_def = None
            for step in wf_def.steps:
                if step.name == current_step_def.next:
                    next_step_def = step
                    break
            if next_step_def is None:
                return {
                    "SKIP_AGENT": "true",
                    "REASON": f"force-next-step: step '{current_step_in_wf}' references next step '{current_step_def.next}' which does not exist in workflow '{config_workflow}'",
                }
            entry = _resolve_step_entry(
                next_step_def, wf_def, workflows_dir, prompt_base,
                scaffold_branch,
            )
            if entry.get("skip_reason"):
                return {"SKIP_AGENT": "true", "REASON": entry["skip_reason"]}
            return _build_force_next_result(
                entry, config_step=next_step_def.name,
                config_workflow=config_workflow, fs_state=fs_state,
            )
        else:
            return {
                "SKIP_AGENT": "true",
                "REASON": "force-next-step: no current step in workflow state",
            }

    # Normal /workflow command normalization (human only)
    wf_name = extract_workflow_command(trigger_body)
    wf_resolved = False
    task = ""
    wf_fail_reason = ""

    if wf_name and _SAFE_WF_NAME_RE.match(wf_name):
        try:
            wf_file = load_workflow_with_dirs(
                wf_name, workflows_dir, local_workflows_dir
            )
            wf_def = load_workflow(str(wf_file))
            task = wf_def.trigger.agents[0] if wf_def.trigger.agents else ""
            if task:
                wf_resolved = True
        except FileNotFoundError as e:
            # Multi-dir miss: the exception message enumerates every path
            # searched (issue #529).
            print(f"Warning: {e}, falling back to @@mention", file=sys.stderr)
            wf_fail_reason = f"/workflow '{wf_name}': {e}"
        except ValueError as e:
            # A schema-invalid file (local override or built-in) still wins —
            # fail loudly instead of silently falling back to another file of
            # the same name (issue #529).
            print(
                f"Warning: workflow '{wf_name}' is schema-invalid ({e}); "
                f"no silent fallback to another directory",
                file=sys.stderr,
            )
            wf_fail_reason = f"/workflow '{wf_name}': schema-invalid ({e})"

    if not wf_resolved:
        # Fall through to @@mention if /workflow was invalid/unresolvable.
        # pinned_task covers CI plain-mention triggers whose bot marker would
        # otherwise make extract_mention_agent return None (issue #478).
        task = pinned_task or extract_mention_agent(trigger_body) or ""
        wf_name = None

    result = {
        "task": task,
        "config_workflow": wf_name if wf_name and wf_resolved else "",
        "config_step": "",
        "config_complexity": "medium",
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
        "config_parent_workflow": "",
        "config_parent_workflow_step": "",
    }
    # Diagnostic for the terminal skip comment when /workflow failed and no
    # mention fallback produced a task (issue #529).
    if wf_fail_reason and not task:
        result["config_skip_reason"] = wf_fail_reason
    return result


def _resolve_bot_fallback_path(
    trigger_body: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve config for a bot comment without a dispatch object.

    Checks for dispatch in workflow state, or returns SKIP_AGENT.

    Args:
        trigger_body: Raw trigger body text.
        workflows_dir: Path to workflows directory.

    Returns:
        Config dict with task and config_* variables, or SKIP_AGENT dict.
    """
    dispatch = extract_dispatch_from_state(trigger_body)
    if not dispatch:
        # Distinguish "agent ended without any sentinel" from "a sentinel
        # marker was present but no usable dispatch was stored" (issue #512)
        # so the skip reason is diagnosable rather than generic.
        if has_agent_run_config_marker(trigger_body):
            reason = (
                "Bot comment contains an AGENT_RUN_CONFIG sentinel but no "
                "usable dispatch was stored (unparseable or invalid sentinel)"
            )
        else:
            reason = "Bot comment without AGENT_RUN_CONFIG or dispatch"
        return {"SKIP_AGENT": "true", "REASON": reason}

    config = {
        "task": dispatch.task_hint or "",
        "config_complexity": dispatch.complexity or "medium",
        "config_reasoning": dispatch.reasoning or "none",
        "config_temperature": str(dispatch.temperature) if dispatch.temperature is not None else "",
        "config_round": "",
        "config_model": dispatch.model or "",
        "config_skills": dispatch.skills or "",
        "config_top_p": str(dispatch.top_p) if dispatch.top_p is not None else "",
        "config_top_k": str(dispatch.top_k) if dispatch.top_k is not None else "",
        "config_repetition_penalty": (
            str(dispatch.repetition_penalty) if dispatch.repetition_penalty is not None else ""
        ),
        "config_debug": str(dispatch.debug).lower() if dispatch.debug else "",
        "config_workflow": dispatch.workflow or "",
        "config_step": dispatch.step or "",
        "config_branch": dispatch.branch or "",
        "config_parent_workflow": dispatch.parent_workflow or "",
        "config_parent_workflow_step": dispatch.parent_workflow_step or "",
    }

    # Resolve next agent based on dispatch status
    if dispatch.status == "advance":
        if not config["task"]:
            # Auto-resolve next agent from workflow YAML
            wf_result = resolve_next_from_workflow(
                config["config_workflow"], config["config_step"], workflows_dir,
                local_workflows_dir=local_workflows_dir,
            )
            if wf_result:
                if wf_result.get("run_script"):
                    # Deterministic script successor (issue #541): no agent task;
                    # build_result short-circuits on this.
                    config.pop("task", None)
                    config["run_script"] = {
                        "path": wf_result.get("script_path", ""),
                        "args_str": wf_result.get("script_args_str", ""),
                        "timeout_minutes": wf_result.get("timeout_minutes"),
                    }
                    config["config_step"] = wf_result.get("step", config["config_step"])
                else:
                    config["task"] = wf_result["task"]
                    config["config_step"] = wf_result.get("step", config["config_step"])
    elif dispatch.status == "loop":
        # Loop: re-dispatch current agent — use task_hint or resolve from state
        config["task"] = dispatch.task_hint or ""
        if not config["task"]:
            prev_state = extract_state_from_body(trigger_body)
            if prev_state:
                config["task"] = prev_state.agent or ""
    elif dispatch.status == "hold":
        return {"SKIP_AGENT": "true", "REASON": "Dispatch status: hold"}
    elif dispatch.status == "terminate":
        return {"SKIP_AGENT": "true", "REASON": "Dispatch status: terminate"}

    return config


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
    dispatch_obj: Optional[Any],
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve next step from workflow YAML when state exists but no dispatch.

    Uses prev_state.workflow + prev_state.step to look up the next agent
    in the workflow definition. When the scalar step is empty (legacy/corrupt
    states), falls back to the state's history (issue #458).

    Args:
        prev_state: Workflow state containing workflow name and current step.
        dispatch_obj: Optional dispatch object (expected to be None).
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
        "config_complexity": "medium",
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
    )
    if wf_result:
        if wf_result.get("run_script"):
            # Deterministic script successor (issue #541): same treatment as
            # the dispatch-driven path — build_result short-circuits on this.
            config["run_script"] = {
                "path": wf_result.get("script_path", ""),
                "args_str": wf_result.get("script_args_str", ""),
                "timeout_minutes": wf_result.get("timeout_minutes"),
            }
            config["config_step"] = wf_result.get("step", config["config_step"])
            if wf_result.get("branch"):
                config["config_branch"] = wf_result["branch"]
        else:
            config["task"] = wf_result["task"]
            config["config_step"] = wf_result.get("step", config["config_step"])
            if wf_result.get("branch"):
                config["config_branch"] = wf_result["branch"]
            _apply_wf_marker(config, wf_result)
    else:
        return _workflow_next_step_skip_reason(wf_name, current_step, workflows_dir)

    return config


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
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Terminal step '{current_step}' has no next in workflow '{wf_name}'",
        }
    # Step has a `next` (or conditions) but no successor resolved —
    # e.g. target step missing from the workflow definition.
    return {
        "SKIP_AGENT": "true",
        "REASON": f"Resolution failed: step '{current_step}' in workflow '{wf_name}' has a next reference that does not resolve",
    }

