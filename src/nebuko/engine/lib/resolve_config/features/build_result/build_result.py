"""Result building feature.

Validates the resolved task, routes via resolve_workflow_routing, and
constructs the final KEY=value environment variable dict.
"""

from typing import Optional

from resolve_workflow import (
    resolve_workflow_routing,
    build_workflow_position,
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
    single ref. The resolver's own entry path already emits it (keep it
    verbatim for exact fidelity), so only non-entry results are rebuilt
    here from the leaf pair + the recorded parent pair.
    """
    if result.get("WORKFLOW_POSITION"):
        # Entry path already emitted the position (resolver fidelity).
        return result
    leaf_wf = result.get("WORKFLOW_NAME", "")
    leaf_step = result.get("WORKFLOW_STEP", "")
    parent_wf = result.get("PARENT_WORKFLOW", "")
    if not leaf_wf:
        # No leaf resolvable — nothing to address (stays position-less).
        return result
    pair = build_workflow_position(
        leaf_wf, leaf_step, parent_wf,
        result.get("PARENT_WORKFLOW_STEP", ""),
        workflows_dir,
        scaffold_branch=config.get("config_scaffold_branch") or None,
        local_workflows_dir=local_workflows_dir,
    )
    return result | pair


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

    # Determine complexity for dev/ctx agents
    complexity = "N/A"
    if role_name in ("dev", "ctx"):
        comp_lower = config.get("config_complexity", "medium").lower() if config.get("config_complexity") else "medium"
        complexity = comp_lower if comp_lower in ("low", "high") else "medium"

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
        "LLM_REASONING": config.get("config_reasoning", "none"),
        "AGENT_ROUND": config.get("config_round", ""),
        "AGENT_SKILLS": config.get("config_skills", ""),
        "STEP_SKILLS": wf_result.get("STEP_SKILLS", ""),
        "STEP_PROMPT_FILE": wf_result.get("STEP_PROMPT_FILE", ""),
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
        "SKIP_AGENT": "false",
    }
    result["WORKFLOW_POSITION"] = wf_result.get("WORKFLOW_POSITION", "")
    result["WORKFLOW_POSITION_PATH"] = wf_result.get("WORKFLOW_POSITION_PATH", "")
    return _attach_position(result, config, workflows_dir, local_workflows_dir)
