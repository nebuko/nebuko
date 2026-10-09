"""Result building feature.

Validates the resolved task, routes via resolve_workflow_routing, and
constructs the final KEY=value environment variable dict.
"""

from typing import Optional

from config import StepRef, WorkflowPosition
from resolve_workflow import (
    resolve_workflow_routing,
    build_workflow_position,
    build_workflow_position_value,
    attach_call_frames,
    build_step_inputs,
    _find_workflow_step,
    _load_workflow_by_reference,
    _position_pair_from_value,
    resolve_complexity_tier,
    TASK_TO_PROMPT,
)

__all__ = ["_build_result"]


def _attach_position(
    result: dict,
    config: dict,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
) -> dict:
    """Centralized position emission (issue #618, review fix for defect 3).

    Every agent result carries a position (issue #646: universal) —
    sub-workflow runs the full mount chain, top-level runs the bare
    single ref. The resolver's own entry path already emits it (kept
    verbatim for exact fidelity). Otherwise the previous run's position is
    carried forward and mutated by this transition (issue #812); rebuilding
    from the lossy flat parent pair is only the fallback for a legacy or
    absent base — that rebuild is what previously truncated a nested run.
    """
    if result.get("WORKFLOW_POSITION"):
        # Entry path already emitted the position (resolver fidelity). The
        # resolver rebuilds that chain from the *current* run's workflow only,
        # so a depth-2 entry (entering a leaf from a middle workflow that is
        # itself mounted) drops every ancestor above the caller; underpin the
        # emitted tail with the carried-forward base so the full mount chain
        # survives to the next job (issue #813).
        return _splice_entry_position(result, config.get("config_position"))
    leaf_wf = result.get("WORKFLOW_NAME", "")
    leaf_step = result.get("WORKFLOW_STEP", "")
    if not leaf_wf:
        # No leaf resolvable — nothing to address (stays position-less).
        return result
    parent_wf = result.get("PARENT_WORKFLOW", "")
    parent_step = result.get("PARENT_WORKFLOW_STEP", "")
    scaffold_branch = config.get("config_scaffold_branch") or None
    base = config.get("config_position")
    if isinstance(base, WorkflowPosition) and base.refs:
        position = _carry_forward_position(
            base, leaf_wf, leaf_step, parent_wf, parent_step,
            workflows_dir, scaffold_branch, local_workflows_dir,
        )
        return result | _position_pair_from_value(position)
    return result | build_workflow_position(
        leaf_wf, leaf_step, parent_wf, parent_step, workflows_dir,
        scaffold_branch=scaffold_branch,
        local_workflows_dir=local_workflows_dir,
    )


def _splice_entry_position(result: dict, base: Optional[WorkflowPosition]) -> dict:
    """Underpin a resolver-emitted entry position with the carried-forward base.

    The resolver's entry path rebuilds the chain with ``_build_position_chain``
    starting from the *current* run's workflow, so entering a leaf from a middle
    workflow that is itself mounted truncates every ancestor above the caller.
    Those enclosing frames are exactly what the carried-forward position still
    records, so the emitted tail is spliced beneath the deepest base frame owned
    by its first element. A missing/foreign base leaves the emitted pair as-is
    (the top-level and legacy cases, which the resolver already renders whole).
    """
    if not isinstance(base, WorkflowPosition) or not base.refs:
        return result
    entry = WorkflowPosition.from_env(result.get("WORKFLOW_POSITION", ""))
    if entry is None or not entry.refs:
        return result
    index = _deepest_workflow_index(base.refs, entry.refs[0].workflow)
    if index is None:
        return result
    merged = WorkflowPosition(
        version=base.version, refs=[*base.refs[:index], *entry.refs]
    )
    return result | _position_pair_from_value(merged)


def _carry_forward_position(
    base: WorkflowPosition,
    leaf_wf: str,
    leaf_step: str,
    parent_wf: str,
    parent_step: str,
    workflows_dir: str,
    scaffold_branch: Optional[str],
    local_workflows_dir: Optional[str],
) -> WorkflowPosition:
    """Apply one transition to the carried-forward mount stack (issue #812).

    The prior position is authoritative. An intra-workflow move (no mount
    boundary — ``parent_wf`` empty and the leaf workflow unchanged) replaces
    just the leaf frame, so every ancestor survives. A mount boundary
    (entry/re-entry/exit, signalled by a resolved ``parent_wf``) re-derives
    the freshly resolved tail and splices it beneath the base frames that
    still enclose it — those frames are carried forward even when the flat
    pair only records the outermost level, which is what the old rebuild
    could not represent.
    """
    leaf = StepRef(workflow=leaf_wf, step=leaf_step)
    if not parent_wf and leaf_wf == base.refs[-1].workflow:
        return base.with_step(leaf)
    rebuilt = build_workflow_position_value(
        leaf_wf, leaf_step, parent_wf, parent_step, workflows_dir,
        scaffold_branch=scaffold_branch,
        local_workflows_dir=local_workflows_dir,
    )
    if rebuilt is None or not rebuilt.refs:
        return base.with_step(leaf)
    index = _deepest_workflow_index(base.refs, rebuilt.refs[0].workflow)
    if index is None:
        return rebuilt
    return WorkflowPosition(
        version=base.version, refs=[*base.refs[:index], *rebuilt.refs]
    )


def _deepest_workflow_index(refs: list, workflow: str) -> Optional[int]:
    """Index of the innermost base frame owned by ``workflow`` (issue #812).

    The splice point for a mount boundary: frames above this index enclose
    the transition and are carried forward; frames at or below it are
    replaced by the freshly resolved tail. ``None`` when the base does not
    contain the boundary's workflow (a wholly new branch), meaning the
    rebuilt tail stands alone.
    """
    for index in range(len(refs) - 1, -1, -1):
        if refs[index].workflow == workflow:
            return index
    return None


def _build_result(
    config: dict,
    prompt_base: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Build the final result dict from resolved config values.

    Validates the task, routes via resolve_workflow_routing, and
    constructs the full environment variable dict.

    Args:
        config: Config dict with task and config_* variables.
        prompt_base: Path to prompt files directory.
        workflows_dir: Path to workflows directory.
        local_workflows_dir: Optional consumer-local workflows dir that takes
            precedence over ``workflows_dir`` (issue #529).

    Returns:
        Dict with all environment variables to set.
    """
    # Deterministic script step (issue #541): no agent runs — emit the script
    # execution contract and let GHA branch on RUN_SCRIPT before agent steps.
    run_script = config.get("run_script")
    if run_script:
        script_result = {
            "TASK": "",
            "ROLE_NAME": "script",
            "AGENT_SELECTED": "false",
            "SKIP_AGENT": "true",
            "RUN_SCRIPT": "true",
            "SCRIPT_PATH": run_script.get("path", ""),
            "SCRIPT_ARGS": run_script.get("args_str", ""),
            "SCRIPT_TIMEOUT_MINUTES": str(run_script.get("timeout_minutes") or ""),
            "WORKFLOW_NAME": config.get("config_workflow", ""),
            "WORKFLOW_STEP": config.get("config_step", ""),
            "PARENT_WORKFLOW": config.get("config_parent_workflow", ""),
            "PARENT_WORKFLOW_STEP": config.get("config_parent_workflow_step", ""),
        }
        # Centralized v2 position emission (issue #618): the script path
        # returns before routing, so attach the chain here from the flat
        # leaf + parent pairs.
        return _attach_position(
            script_result, config, workflows_dir, local_workflows_dir
        )

    task = config.get("task", "")
    if not task:
        # A malformed or unknown human slash command outranks the workflow-name
        # skip reason: the invocation can never be valid, so its usage hint is
        # the actionable message (issue #804). The COMMAND_ERROR flag tells the
        # engine this terminal skip is owed a notice on the thread.
        command_error = config.get("config_command_error") or ""
        if command_error:
            return {
                "SKIP_AGENT": "true",
                "REASON": command_error,
                "COMMAND_ERROR": "true",
            }
        # Enriched reason: carries /nebuko failure detail (name + searched
        # directories) when available (issue #529).
        skip_reason = config.get("config_skip_reason") or "No valid task found"
        return {"SKIP_AGENT": "true", "REASON": skip_reason}

    # Validate task
    if task not in TASK_TO_PROMPT:
        return {"SKIP_AGENT": "true", "REASON": f"Unknown task: {task}"}

    # Workflow-aware routing
    wf_result = resolve_workflow_routing(
        {
            "task": task,
            "complexity": config.get("config_complexity", "medium"),
            "reasoning": config.get("config_reasoning", "none"),
            "model": config.get("config_model", ""),
            "temperature": config.get("config_temperature", ""),
            "round": config.get("config_round", ""),
            "skills": config.get("config_skills", ""),
            "top_p": config.get("config_top_p", ""),
            "top_k": config.get("config_top_k", ""),
            "repetition_penalty": config.get("config_repetition_penalty", ""),
            "workflow": config.get("config_workflow", ""),
            "step": config.get("config_step", ""),
            "branch": config.get("config_branch", ""),
            "parent_workflow": config.get("config_parent_workflow", ""),
            "parent_workflow_step": config.get("config_parent_workflow_step", ""),
        },
        workflows_dir,
        prompt_base,
        local_workflows_dir,
        scaffold_branch=config.get("config_scaffold_branch") or None,
    )

    if wf_result.get("SKIP_AGENT") == "true":
        return wf_result

    role_name = wf_result["ROLE_NAME"]
    prompt_location = wf_result["PROMPT_LOCATION"]

    # Effective complexity tier (issue #787): explicit dispatch complexity
    # wins, else the workflow step's declared tier, else "medium".
    effective_complexity = resolve_complexity_tier(
        config.get("config_complexity", ""),
        wf_result.get("STEP_COMPLEXITY", ""),
    )

    # Determine complexity for dev/context agents
    complexity = "N/A"
    if role_name in ("dev", "context"):
        complexity = effective_complexity

    # Build matched rule identifier for debugging
    wf_name = wf_result.get("WORKFLOW_NAME", "")
    wf_step = wf_result.get("WORKFLOW_STEP", "")
    if wf_name and wf_step:
        matched_rule = f"workflow:{wf_name}/step:{wf_step}"
    elif wf_name:
        matched_rule = f"workflow:{wf_name}"
    else:
        matched_rule = ""

    # Build result
    result = {
        "TASK": task,
        "ROLE_NAME": role_name,
        "PROMPT_LOCATION": prompt_location,
        "AGENT_SELECTED": "true",
        "COMPLEXITY": complexity,
        # CONFIG_COMPLEXITY drives the runtime tier machinery (issue #787):
        # resolve_llm_config applies agents.<name>.complexity_overrides.<tier>
        # and the complexity->temperature floor. Emitted for every role, not
        # just dev/context, so cheap tiers also apply to architect/review steps.
        "CONFIG_COMPLEXITY": effective_complexity,
        "LLM_REASONING": config.get("config_reasoning", "none"),
        "AGENT_ROUND": config.get("config_round", ""),
        "AGENT_SKILLS": config.get("config_skills", ""),
        "STEP_SKILLS": wf_result.get("STEP_SKILLS", ""),
        "STEP_PROMPT_FILE": wf_result.get("STEP_PROMPT_FILE", ""),
        "STEP_PROMPT_REPLACE": wf_result.get("STEP_PROMPT_REPLACE", ""),
        "CONFIG_MODEL": config.get("config_model", ""),
        "CONFIG_TEMPERATURE": config.get("config_temperature", ""),
        "CONFIG_TOP_P": config.get("config_top_p", ""),
        "CONFIG_TOP_K": config.get("config_top_k", ""),
        "CONFIG_REPETITION_PENALTY": config.get("config_repetition_penalty", ""),
        "CONFIG_DEBUG": config.get("config_debug", ""),
        "WORKFLOW_NAME": wf_result.get("WORKFLOW_NAME", ""),
        "WORKFLOW_STEP": wf_result.get("WORKFLOW_STEP", ""),
        "WORKFLOW_NEXT": wf_result.get("WORKFLOW_NEXT", ""),
        "PARENT_WORKFLOW": wf_result.get("PARENT_WORKFLOW", ""),
        "PARENT_WORKFLOW_STEP": wf_result.get("PARENT_WORKFLOW_STEP", ""),
        # Callback return address (issue #765): the caller agent this run must
        # fall back to when it finishes with no decision of its own.
        "CONFIG_CALLBACK": config.get("config_callback", ""),
        # v2 mount-chain position (issue #618): attached below by
        # ``_attach_position`` (entry-path emission is kept verbatim; all
        # other sub-workflow runs rebuild the full chain from the leaf +
        # parent pairs so every run posts a non-None position).
        "MATCHED_RULE": matched_rule,
        "HUMAN_RESUME_INPUT": config.get("config_human_resume_input", ""),
        # Engine soft-kill retry marker (issue #704): set only by
        # ``resolve_soft_kill_route``'s retry config. ``build_output`` forwards
        # it to ``build_state`` so the per-step budget is consumed for genuine
        # retries and never for a human/manual trigger.
        "SOFT_RETRY_ATTEMPT": config.get("config_soft_retry_attempt", ""),
        # Engine-owned gate decision (issue #648): set only by the
        # ``/nebuko approve|reject gate`` resolution. ``build_output`` parses it
        # and ``build_state`` persists it — the release is recorded in workflow
        # state, outside the agent loop.
        "GATE_DECISION": config.get("config_gate_decision", ""),
        "SKIP_AGENT": "false",
    }
    result["WORKFLOW_POSITION"] = wf_result.get("WORKFLOW_POSITION", "")
    result["WORKFLOW_POSITION_PATH"] = wf_result.get("WORKFLOW_POSITION_PATH", "")
    result = _attach_position(result, config, workflows_dir, local_workflows_dir)
    return _attach_step_inputs(result, config, workflows_dir, local_workflows_dir)


def _attach_step_inputs(
    result: dict,
    config: dict,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
) -> dict:
    """Emit a resolved step's declared input wiring as ``STEP_INPUTS`` (issue #813).

    The payload is the step's own ``inputs:`` bindings plus its workflow's
    declared input signature (the ``required`` policy). The mount-chain frames
    are derived from the run's *final* position — the carried-forward chain is
    the only place the immediate caller of each level is recorded accurately,
    which is what makes kind-preserving lazy forwarding work across nesting.
    Steps that declare no inputs are left untouched, keeping their resolver
    output byte-identical.
    """
    wf_name = result.get("WORKFLOW_NAME") or ""
    step_name = result.get("WORKFLOW_STEP") or ""
    if not wf_name or not step_name:
        return result
    scaffold_branch = config.get("config_scaffold_branch") or None
    workflow = _load_workflow_by_reference(
        wf_name, workflows_dir, local_workflows_dir, scaffold_branch,
    )
    step = _find_workflow_step(workflow, step_name) if workflow else None
    if step is None or not step.inputs:
        return result
    result["STEP_INPUTS"] = attach_call_frames(
        build_step_inputs(workflow, step),
        result.get("WORKFLOW_POSITION", ""),
        workflows_dir,
        local_workflows_dir,
        scaffold_branch,
    )
    return result
