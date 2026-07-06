#!/usr/bin/env python3
"""
Resolve agent configuration from trigger body.

Replaces the 223-line 'Resolve Agent Configuration' bash block.
Extracts agentRunConfig JSON, parses fields, routes via resolve_workflow.py,
and outputs environment variables.

Usage:
    python resolve_config.py \\
        --trigger-body <file|-> \\
        --prompt-base <path> \\
        --workflows-dir <path> \\
        --is-bot-comment <true|false>
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

# Import resolve_workflow directly (same directory)
sys.path.insert(0, os.path.dirname(__file__))
from config import load_workflow
from resolve_workflow import (
    resolve_workflow_routing,
    resolve_next_from_workflow,
    TASK_TO_PROMPT,
)
from workflow_state import (
    extract_dispatch_from_state,
    extract_state_from_body,
    extract_latest_state_from_comments,
)
from github_client import get_issue_comments

# Regex for safe workflow names: alphanumeric, hyphens, underscores only
_SAFE_WF_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Maximum consecutive same-agent loops before blocking (loop safeguard)
MAX_LOOP_COUNT = 5


def resolve_next_agent_from_workflow(
    task_hint: Optional[str],
    workflow_name: str,
    step_name: str,
    workflows_dir: str,
) -> Optional[str]:
    """Resolve next agent from workflow YAML definition.

    If task_hint is provided, use it directly. Otherwise, look up the
    workflow YAML to find the `next` agent for the current step.

    Args:
        task_hint: Optional explicit next agent override.
        workflow_name: Name of the active workflow.
        step_name: Current step name.
        workflows_dir: Path to workflows directory.

    Returns:
        Next agent name or None if unresolvable.
    """
    if task_hint:
        return task_hint

    if not workflow_name or not step_name:
        return None

    wf_file = Path(workflows_dir) / f"{workflow_name}.yaml"
    try:
        wf_def = load_workflow(str(wf_file))
    except (FileNotFoundError, ValueError):
        return None

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


def is_bot_comment(trigger_body: str) -> bool:
    """Check if trigger body starts with a bot-agent marker.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if first 5 lines contain <!-- bot-agent-.
    """
    first_lines = "\n".join(trigger_body.split("\n")[:5])
    return "<!-- bot-agent-" in first_lines


def extract_mention_agent(trigger_body: str) -> Optional[str]:
    """Extract the last @@mention agent name from trigger body.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Agent name string or None.
    """
    agents = re.findall(
        r"@@(?:issue-reviewer|genius|ctx|dev|review|issue|summarize)",
        trigger_body,
    )
    if agents:
        return agents[-1].replace("@@", "")
    return None


def extract_workflow_command(trigger_body: str) -> Optional[str]:
    """Extract /workflow command from the first line of trigger body.

    Only matches if the first non-empty line starts with "/workflow <name>".
    Returns the workflow name or None.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Workflow name string or None.
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+(\S+)", first_line)
    if match:
        return match.group(1)
    return None


def extract_force_next_command(trigger_body: str) -> Optional[tuple]:
    """Extract '/workflow force-next-step' command from the first line.

    Supports optional target step: '/workflow force-next-step implement'.
    Returns ('force-next-step', target_step_or_none) or None.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Tuple of ('force-next-step', target_step) or None.
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+force-next-step(?:\s+(\S+))?$", first_line)
    if match:
        target_step = match.group(1)  # Optional explicit target step name
        return ("force-next-step", target_step)
    return None


def extract_resume_command(trigger_body: str) -> Optional[str]:
    """Extract '/workflow resume' command from the first line.

    Returns the human-provided input text (everything after '/workflow resume')
    or None if the command is not present. If the command is present but has
    no trailing text, returns an empty string.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Human input text (str), empty string if no input, or None if not a resume command.
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+resume(?:\s+(.+))?$", first_line)
    if match:
        return match.group(1) if match.group(1) is not None else ""
    return None


def extract_debug_flag(trigger_body: str) -> bool:
    """Extract DEBUG=true flag from trigger body.

    Looks for 'DEBUG=true' (case-insensitive) anywhere in the comment body,
    including as part of /workflow commands like '/workflow default DEBUG=true'.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if DEBUG=true is found, False otherwise.
    """
    return bool(re.search(r"DEBUG\s*=\s*true", trigger_body, re.IGNORECASE))


# ── Helper methods for resolve_config ──────────────────────────────────────


def _parse_trigger_body(trigger_body: str) -> tuple:
    """Extract workflow state and dispatch from the current trigger body.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Tuple of (prev_state, dispatch_obj).
    """
    prev_state = extract_state_from_body(trigger_body)
    dispatch_obj = prev_state.dispatch if prev_state else None
    return prev_state, dispatch_obj


def _fetch_workflow_state_from_history(
    repo: Optional[str],
    issue_number: Optional[int],
) -> Optional["WorkflowState"]:
    """Fetch latest workflow state from issue comment history.

    Args:
        repo: Repository in owner/name format.
        issue_number: Issue/PR number.

    Returns:
        Latest WorkflowState from comments, or None.
    """
    if not repo or not issue_number:
        return None
    try:
        comments = get_issue_comments(repo, issue_number)
        return extract_latest_state_from_comments(comments)
    except Exception:
        return None


def _resolve_resume_path(
    prev_state: "WorkflowState",
    trigger_body: str,
    workflows_dir: str,
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

    wf_file = Path(workflows_dir) / f"{wf_name}.yaml"
    try:
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

    # Extract human input text from the resume command
    human_input = extract_resume_command(trigger_body)
    if human_input is None:
        human_input = ""

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


def _resolve_dispatch_path(
    prev_state: "WorkflowState",
    dispatch_obj: "Dispatch",
    workflows_dir: str,
) -> dict:
    """Resolve config when a dispatch object is present in the trigger body.

    Handles dispatch statuses (advance, loop, hold, terminate) and
    workflow-driven step progression.

    Args:
        prev_state: WorkflowState from the trigger body.
        dispatch_obj: Dispatch object extracted from prev_state.
        workflows_dir: Path to workflows directory.

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
        wf_result = resolve_next_from_workflow(
            prev_state.workflow,
            prev_state.step,
            workflows_dir,
            agent_hint=None,
            branch_result=branch_result,
        )
        if wf_result:
            config["task"] = wf_result["task"]
            config["config_step"] = wf_result.get("step", config["config_step"])
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


def _resolve_human_path(
    trigger_body: str,
    repo: Optional[str],
    issue_number: Optional[int],
    workflows_dir: str,
    prev_state: Optional["WorkflowState"] = None,
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
        config_workflow = fs_state.workflow
        if not _SAFE_WF_NAME_RE.match(config_workflow):
            return {
                "SKIP_AGENT": "true",
                "REASON": f"force-next-step: invalid workflow name '{config_workflow}'",
            }
        current_step_in_wf = fs_state.step
        wf_file_force = Path(workflows_dir) / f"{config_workflow}.yaml"
        try:
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
            # Explicit target step — validate it exists in the workflow
            step_found = False
            for step in wf_def.steps:
                if step.name == target_step:
                    step_found = True
                    if not step.agent:
                        return {
                            "SKIP_AGENT": "true",
                            "REASON": f"force-next-step: step '{target_step}' has no agent defined",
                        }
                    config_step = target_step
                    task = step.agent
                    break
            if not step_found:
                return {
                    "SKIP_AGENT": "true",
                    "REASON": f"force-next-step: step '{target_step}' not found in workflow '{config_workflow}'",
                }
        elif current_step_in_wf:
            # Auto-advance: find current step and get its .next (skip conditions)
            for step in wf_def.steps:
                if step.name == current_step_in_wf:
                    if not step.next:
                        return {
                            "SKIP_AGENT": "true",
                            "REASON": f"force-next-step: current step '{current_step_in_wf}' is terminal (no next step)",
                        }
                    for next_s in wf_def.steps:
                        if next_s.name == step.next:
                            if not next_s.agent:
                                return {
                                    "SKIP_AGENT": "true",
                                    "REASON": f"force-next-step: next step '{next_s.name}' has no agent defined",
                                }
                            config_step = next_s.name
                            task = next_s.agent
                            break
                    else:
                        return {
                            "SKIP_AGENT": "true",
                            "REASON": f"force-next-step: step '{current_step_in_wf}' references next step '{step.next}' which does not exist in workflow '{config_workflow}'",
                        }
                    break
            else:
                return {
                    "SKIP_AGENT": "true",
                    "REASON": f"force-next-step: current step '{current_step_in_wf}' not found in workflow '{config_workflow}'",
                }
        else:
            return {
                "SKIP_AGENT": "true",
                "REASON": "force-next-step: no current step in workflow state",
            }

        return {
            "task": task,
            "config_step": config_step,
            "config_workflow": config_workflow,
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
            "config_parent_workflow": fs_state.parent_workflow or "",
            "config_parent_workflow_step": fs_state.parent_workflow_step or "",
        }

    # Normal /workflow command normalization (human only)
    wf_name = extract_workflow_command(trigger_body)
    wf_resolved = False
    task = ""

    if wf_name and _SAFE_WF_NAME_RE.match(wf_name):
        wf_file = Path(workflows_dir) / f"{wf_name}.yaml"
        try:
            wf_def = load_workflow(str(wf_file))
            task = wf_def.trigger.agents[0] if wf_def.trigger.agents else ""
            if task:
                wf_resolved = True
        except FileNotFoundError:
            print(
                f"Warning: workflow '{wf_name}' not found at {wf_file}, "
                f"falling back to @@mention",
                file=sys.stderr,
            )
        except ValueError as e:
            print(
                f"Warning: workflow '{wf_name}' invalid ({e}), "
                f"falling back to @@mention",
                file=sys.stderr,
            )

    if not wf_resolved:
        # Fall through to @@mention if /workflow was invalid/unresolvable
        task = extract_mention_agent(trigger_body) or ""
        wf_name = None

    return {
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


def _resolve_bot_fallback_path(
    trigger_body: str,
    workflows_dir: str,
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
        return {"SKIP_AGENT": "true", "REASON": "Bot comment without agentRunConfig or dispatch"}

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
                config["config_workflow"], config["config_step"], workflows_dir
            )
            if wf_result:
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


def _resolve_workflow_next_step(
    prev_state: "WorkflowState",
    dispatch_obj: Optional[Any],
    workflows_dir: str,
) -> dict:
    """Resolve next step from workflow YAML when state exists but no dispatch.

    Uses prev_state.workflow + prev_state.step to look up the next agent
    in the workflow definition.

    Args:
        prev_state: Workflow state containing workflow name and current step.
        dispatch_obj: Optional dispatch object (expected to be None).
        workflows_dir: Path to workflows directory.

    Returns:
        Config dict with resolved task and config_* variables.
    """
    wf_name = prev_state.workflow
    current_step = prev_state.step

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
    wf_result = resolve_next_from_workflow(wf_name, current_step, workflows_dir)
    if wf_result:
        config["task"] = wf_result["task"]
        config["config_step"] = wf_result.get("step", config["config_step"])
        if wf_result.get("branch"):
            config["config_branch"] = wf_result["branch"]
    else:
        return {
            "SKIP_AGENT": "true",
            "REASON": "Terminal step reached or workflow resolution failed",
        }

    return config


def _build_result(
    config: dict,
    prompt_base: str,
    workflows_dir: str,
) -> dict:
    """Build the final result dict from resolved config values.

    Validates the task, routes via resolve_workflow_routing, and
    constructs the full environment variable dict.

    Args:
        config: Config dict with task and config_* variables.
        prompt_base: Path to prompt files directory.
        workflows_dir: Path to workflows directory.

    Returns:
        Dict with all environment variables to set.
    """
    task = config.get("task", "")
    if not task:
        return {"SKIP_AGENT": "true", "REASON": "No valid task found"}

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
        "MATCHED_RULE": matched_rule,
        "HUMAN_RESUME_INPUT": config.get("config_human_resume_input", ""),
        "SKIP_AGENT": "false",
    }

    return result


def resolve_config(
    trigger_body: str,
    prompt_base: str,
    workflows_dir: str,
    is_bot: Optional[bool] = None,
    repo: Optional[str] = None,
    issue_number: Optional[int] = None,
    debug: bool = False
) -> dict:
    """Resolve complete agent configuration from trigger body.

    Orchestrates helper methods to determine agent task and parameters:
    1. Parse trigger body for workflow state and dispatch (_parse_trigger_body).
    2. Bot detection (if not pre-determined).
    3. If no state in body, fetch from issue comment history.
    4. Route based on what was found (human commands take precedence):
       - State + dispatch → _resolve_dispatch_path.
       - /workflow force-next-step → _resolve_human_path (regardless of state).
       - /workflow command by human → _resolve_human_path.
       - State without dispatch → _resolve_workflow_next_step.
       - Human trigger without state → _resolve_human_path.
       - Bot fallback → _resolve_bot_fallback_path.
    5. Build result dict with all env vars (_build_result).

    Args:
        trigger_body: Raw trigger body text.
        prompt_base: Path to prompt files directory.
        workflows_dir: Path to workflows directory.
        is_bot: Pre-determined bot status (None = auto-detect).
        repo: Repository in owner/name format (for fetching comments).
        issue_number: Issue/PR number (for fetching comments).

    Returns:
        Dict with all environment variables to set.
    """
    if is_bot is None:
        is_bot = is_bot_comment(trigger_body)

    # Parse trigger body for workflow state and dispatch
    prev_state, dispatch_obj = _parse_trigger_body(trigger_body)

    # Check for human commands early — they take precedence over workflow state
    # regardless of whether prev_state exists (they need to resolve the command)
    force_next = extract_force_next_command(trigger_body)
    resume_cmd = extract_resume_command(trigger_body)
    wf_override = extract_workflow_command(trigger_body)

    # Centralized fallback: if no state from current body, try comment history
    if not prev_state:
        prev_state = _fetch_workflow_state_from_history(repo, issue_number)
        if prev_state:
            dispatch_obj = prev_state.dispatch

    if os.environ.get("DEBUG") or debug:
        print(f"DEBUG prev_state: {prev_state}", file=sys.stderr)

    # Route to appropriate handler based on what was found
    if resume_cmd is not None and prev_state and prev_state.workflow and \
       prev_state.dispatch and prev_state.dispatch.status == "hold":
        # /workflow resume — re-run the held step with human input
        config = _resolve_resume_path(prev_state, trigger_body, workflows_dir)
    elif resume_cmd is not None and prev_state and prev_state.workflow and \
         (prev_state.dispatch is None):
        # /workflow resume with no dispatch — advance to next step, carrying
        # human input forward (issue #420: resolution-only state).
        human_input = trigger_body.replace("/workflow resume", "").strip()
        config = _resolve_workflow_next_step(prev_state, dispatch_obj, workflows_dir)
        config["config_human_resume_input"] = human_input
    elif prev_state and dispatch_obj:
        config = _resolve_dispatch_path(prev_state, dispatch_obj, workflows_dir)
    elif force_next:
        # Force-next-step is a human command that must always go through
        # _resolve_human_path so it can handle validation/error cases.
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir, prev_state
        )
    elif prev_state and prev_state.workflow and not is_bot and wf_override:
        # Human provided /workflow override — use human path to honor the command
        # even though prev_state.workflow is available from history.
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir, prev_state
        )
    elif prev_state and prev_state.workflow:
        config = _resolve_workflow_next_step(prev_state, dispatch_obj, workflows_dir)
    elif not is_bot:
        config = _resolve_human_path(trigger_body, repo, issue_number, workflows_dir)
    else:
        config = _resolve_bot_fallback_path(trigger_body, workflows_dir)

    if config.get("SKIP_AGENT") == "true":
        return config

    result = _build_result(config, prompt_base, workflows_dir)
    if os.environ.get("DEBUG") or debug:
        print(f"DEBUG _build_result: {result}", file=sys.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Resolve agent configuration from trigger body"
    )
    parser.add_argument(
        "--trigger-body",
        default="-",
        help="Path to trigger body file, or '-' for stdin",
    )
    parser.add_argument(
        "--prompt-base",
        default=".agents/prompts",
        help="Path to prompt files directory",
    )
    parser.add_argument(
        "--workflows-dir",
        default="workflows",
        help="Path to workflows directory",
    )
    parser.add_argument(
        "--is-bot-comment",
        choices=["true", "false"],
        default=None,
        help="Pre-determine bot comment status",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Path to GITHUB_ENV file to append results",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Repository in owner/name format (for fetching comments)",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=None,
        help="Issue/PR number (for fetching comments)",
    )

    args = parser.parse_args()

    # Read trigger body
    if args.trigger_body == "-":
        trigger_body = sys.stdin.read()
    else:
        with open(args.trigger_body, "r", encoding="utf-8") as f:
            trigger_body = f.read()

    is_bot = None
    if args.is_bot_comment == "true":
        is_bot = True
    elif args.is_bot_comment == "false":
        is_bot = False
    
    debug_flag = extract_debug_flag(trigger_body),

    result = resolve_config(
        trigger_body=trigger_body,
        prompt_base=args.prompt_base,
        workflows_dir=args.workflows_dir,
        is_bot=is_bot,
        repo=args.repo,
        issue_number=args.issue_number,
        debug=debug_flag
    )

    # Output as KEY=value pairs
    for key, value in result.items():
        print(f"{key}={value}")

    # Append to GITHUB_ENV if specified
    if args.env_file:
        with open(args.env_file, "a") as f:
            for key, value in result.items():
                f.write(f"{key}={value}\n")

    print(
        f"Resolved agent: ROLE_NAME={result.get('ROLE_NAME', '?')} "
        f"COMPLEXITY={result.get('COMPLEXITY', '?')}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
