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
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

# Import resolve_workflow directly (same directory)
sys.path.insert(0, os.path.dirname(__file__))
from config import load_workflow
from resolve_workflow import (
    resolve_workflow_routing,
    resolve_next_from_workflow,
    TASK_TO_PROMPT,
)
from trigger_parser import parse_trigger
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


def extract_agent_run_config(trigger_body: str) -> Optional[dict]:
    """Extract agentRunConfig JSON block from trigger body text.

    Delegates to trigger_parser for the parsing logic.
    Kept as a wrapper for backward compatibility with existing callers.
    Returns the full raw config dict (not just parsed hints).
    """
    from trigger_parser import _extract_agent_run_config as _raw_extract
    return _raw_extract(trigger_body)


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

    Delegates to trigger_parser for the detection logic.
    """
    return parse_trigger(trigger_body).is_bot_comment


def extract_mention_agent(trigger_body: str) -> Optional[str]:
    """Extract the last @@mention agent name from trigger body.

    Delegates to trigger_parser for the extraction logic.
    """
    return parse_trigger(trigger_body).agent_name


def extract_workflow_command(trigger_body: str) -> Optional[str]:
    """Extract /workflow command from the first line of trigger body.

    Delegates to trigger_parser for the extraction logic.
    """
    return parse_trigger(trigger_body).workflow_name


def extract_force_next_command(trigger_body: str) -> Optional[tuple]:
    """Extract '/workflow force-next-step' command from the first line.

    Delegates to trigger_parser for the extraction logic.
    Wraps the result back into the tuple format expected by downstream callers.
    """
    fn = parse_trigger(trigger_body).force_next_step
    if not fn:
        return None
    if fn == "force-next-step":
        return ("force-next-step", None)
    return ("force-next-step", fn)


def resolve_config(
    trigger_body: str,
    prompt_base: str,
    workflows_dir: str,
    is_bot: Optional[bool] = None,
    repo: Optional[str] = None,
    issue_number: Optional[int] = None,
) -> dict:
    """Resolve complete agent configuration from trigger body.

    This replaces the 223-line bash block. Steps:
    1. Extract agentRunConfig JSON
    2. Bot detection
    3. Parse config or fallback to @@mention
    4. Route via resolve_workflow.py or case-based fallback
    5. Build result dict with all env vars

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

    # Step 1: Try to get dispatch from workflow_state (primary path)
    # Falls back to XML <agentRunConfig> block (backward compatible)
    prev_state = extract_state_from_body(trigger_body)
    dispatch_obj = prev_state.dispatch if prev_state else None

    # Extract XML agentRunConfig as fallback
    xml_config = extract_agent_run_config(trigger_body)

    # Determine which source to use: dispatch from state > XML block
    config_source = None  # 'dispatch' or 'xml'
    if dispatch_obj:
        config_source = 'dispatch'
    elif xml_config:
        config_source = 'xml'

    if config_source == 'dispatch':
        # Dispatch from workflow_state (structured, primary path)
        agent_hint = dispatch_obj.task_hint or ""
        branch_result = dispatch_obj.branch

        config_complexity = dispatch_obj.complexity or "medium"
        config_reasoning = dispatch_obj.reasoning or "none"
        config_temperature = str(dispatch_obj.temperature) if dispatch_obj.temperature is not None else ""
        config_round = ""
        config_model = dispatch_obj.model or ""
        config_skills = json.dumps(dispatch_obj.skills) if dispatch_obj.skills else ""
        config_top_p = str(dispatch_obj.top_p) if dispatch_obj.top_p is not None else ""
        config_top_k = str(dispatch_obj.top_k) if dispatch_obj.top_k is not None else ""
        config_repetition_penalty = str(dispatch_obj.repetition_penalty) if dispatch_obj.repetition_penalty is not None else ""
        config_workflow = dispatch_obj.workflow or ""
        config_step = dispatch_obj.step or ""
        config_branch = dispatch_obj.branch or ""
        config_parent_workflow = dispatch_obj.parent_workflow or ""
        config_parent_workflow_step = dispatch_obj.parent_workflow_step or ""

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
                task = wf_result["task"]
                config_step = wf_result.get("step", config_step)
            else:
                dispatch_status = dispatch_obj.status or ""
                if dispatch_status == "hold":
                    return {"SKIP_AGENT": "true", "REASON": "Dispatch status: hold"}
                elif dispatch_status == "terminate":
                    return {"SKIP_AGENT": "true", "REASON": "Dispatch status: terminate"}
                elif dispatch_status == "loop":
                    task = prev_state.agent or ""
                elif dispatch_status == "advance":
                    task = agent_hint
                else:
                    return {
                        "SKIP_AGENT": "true",
                        "REASON": "Terminal step reached",
                    }
        else:
            task = agent_hint
    elif config_source == 'xml':
        # XML <agentRunConfig> block (backward-compatible fallback)
        agent_hint = xml_config.get("task", "")
        dispatch = xml_config.get("dispatch", {})
        branch_result = dispatch.get("branch") if isinstance(dispatch, dict) else None

        config_complexity = xml_config.get("complexity", "medium")
        config_reasoning = xml_config.get("reasoning", "none")
        config_temperature = xml_config.get("temperature", "")
        config_round = xml_config.get("round", "")
        config_model = xml_config.get("model", "")
        config_skills = xml_config.get("skills", "")
        config_top_p = xml_config.get("top_p", "")
        config_top_k = xml_config.get("top_k", "")
        config_repetition_penalty = xml_config.get("repetition_penalty", "")
        config_workflow = xml_config.get("workflow", "")
        config_step = xml_config.get("step", "")
        config_branch = xml_config.get("branch", "")
        config_parent_workflow = xml_config.get("parent_workflow", "")
        config_parent_workflow_step = xml_config.get("parent_workflow_step", "")

        if prev_state and prev_state.workflow and prev_state.step:
            wf_result = resolve_next_from_workflow(
                prev_state.workflow,
                prev_state.step,
                workflows_dir,
                agent_hint=None,
                branch_result=branch_result,
            )
            if wf_result:
                task = wf_result["task"]
                config_step = wf_result.get("step", config_step)
            else:
                dispatch_status = dispatch.get("status", "") if isinstance(dispatch, dict) else ""
                if dispatch_status == "hold":
                    return {"SKIP_AGENT": "true", "REASON": "Dispatch status: hold"}
                elif dispatch_status == "terminate":
                    return {"SKIP_AGENT": "true", "REASON": "Dispatch status: terminate"}
                elif dispatch_status == "loop":
                    task = prev_state.agent or ""
                elif dispatch_status == "advance":
                    task = agent_hint
                else:
                    return {
                        "SKIP_AGENT": "true",
                        "REASON": "Terminal step reached",
                    }
        else:
            task = agent_hint
    elif not is_bot:
        # Check for force-next-step command first (human only)
        force_next = extract_force_next_command(trigger_body)
        if force_next:
            _, target_step = force_next
            prev_state = extract_state_from_body(trigger_body)
            if not prev_state or not prev_state.workflow:
                # Fallback: fetch workflow state from issue comments
                if repo and issue_number:
                    comments = get_issue_comments(repo, issue_number)
                    prev_state = extract_latest_state_from_comments(comments)
            if not prev_state or not prev_state.workflow:
                return {
                    "SKIP_AGENT": "true",
                    "REASON": "force-next-step requires an active workflow (no workflow state found)",
                }
            config_workflow = prev_state.workflow
            if not _SAFE_WF_NAME_RE.match(config_workflow):
                return {
                    "SKIP_AGENT": "true",
                    "REASON": f"force-next-step: invalid workflow name '{config_workflow}'",
                }
            current_step_in_wf = prev_state.step
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

            config_complexity = "medium"
            config_reasoning = "none"
            config_temperature = ""
            config_round = ""
            config_model = ""
            config_skills = ""
            config_top_p = ""
            config_top_k = ""
            config_repetition_penalty = ""
            config_branch = ""
            config_parent_workflow = prev_state.parent_workflow or ""
            config_parent_workflow_step = prev_state.parent_workflow_step or ""
        else:
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

            config_complexity = "medium"
            config_reasoning = "none"
            config_temperature = ""
            config_round = ""
            config_model = ""
            config_skills = ""
            config_top_p = ""
            config_top_k = ""
            config_repetition_penalty = ""
            config_workflow = wf_name if wf_name and wf_resolved else ""
            config_step = ""
            config_branch = ""
            config_parent_workflow = ""
            config_parent_workflow_step = ""
    else:
        # Bot comment without explicit agentRunConfig — check for dispatch in state
        dispatch = extract_dispatch_from_state(trigger_body)
        if dispatch:
            # Dispatch found in workflow state — resolve routing from it
            task = dispatch.task_hint or ""
            config_complexity = dispatch.complexity or "medium"
            config_reasoning = dispatch.reasoning or "none"
            config_temperature = str(dispatch.temperature) if dispatch.temperature is not None else ""
            config_round = ""
            config_model = dispatch.model or ""
            config_skills = dispatch.skills or ""
            config_top_p = str(dispatch.top_p) if dispatch.top_p is not None else ""
            config_top_k = str(dispatch.top_k) if dispatch.top_k is not None else ""
            config_repetition_penalty = (
                str(dispatch.repetition_penalty) if dispatch.repetition_penalty is not None else ""
            )
            config_workflow = dispatch.workflow or ""
            config_step = dispatch.step or ""
            config_branch = dispatch.branch or ""
            config_parent_workflow = dispatch.parent_workflow or ""
            config_parent_workflow_step = dispatch.parent_workflow_step or ""

            # Resolve next agent based on dispatch status
            if dispatch.status == "advance":
                if not task:
                    # Auto-resolve next agent from workflow YAML
                    wf_result = resolve_next_from_workflow(
                        config_workflow, config_step, workflows_dir
                    )
                    if wf_result:
                        task = wf_result["task"]
                        config_step = wf_result.get("step", config_step)
            elif dispatch.status == "loop":
                # Loop: re-dispatch current agent — use task_hint or resolve from state
                task = dispatch.task_hint or ""
                if not task:
                    prev_state = extract_state_from_body(trigger_body)
                    if prev_state:
                        task = prev_state.agent or ""
            elif dispatch.status == "hold":
                return {"SKIP_AGENT": "true", "REASON": "Dispatch status: hold"}
            elif dispatch.status == "terminate":
                return {"SKIP_AGENT": "true", "REASON": "Dispatch status: terminate"}
        else:
            # No dispatch in state either — skip
            return {"SKIP_AGENT": "true", "REASON": "Bot comment without agentRunConfig or dispatch"}

    if not task:
        return {"SKIP_AGENT": "true", "REASON": "No valid task found"}

    # Validate task
    if task not in TASK_TO_PROMPT:
        return {"SKIP_AGENT": "true", "REASON": f"Unknown task: {task}"}

    # Step 5: Workflow-aware routing
    wf_result = resolve_workflow_routing(
        {
            "task": task,
            "complexity": config_complexity,
            "reasoning": config_reasoning,
            "model": config_model,
            "temperature": config_temperature,
            "round": config_round,
            "skills": config_skills,
            "top_p": config_top_p,
            "top_k": config_top_k,
            "repetition_penalty": config_repetition_penalty,
            "workflow": config_workflow,
            "step": config_step,
            "branch": config_branch,
            "parent_workflow": config_parent_workflow,
            "parent_workflow_step": config_parent_workflow_step,
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
        comp_lower = config_complexity.lower() if config_complexity else "medium"
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
        "LLM_REASONING": config_reasoning,
        "AGENT_ROUND": config_round or "",
        "AGENT_SKILLS": config_skills or "",
        "STEP_SKILLS": wf_result.get("STEP_SKILLS", ""),
        "CONFIG_MODEL": config_model or "",
        "CONFIG_TEMPERATURE": config_temperature or "",
        "CONFIG_TOP_P": config_top_p or "",
        "CONFIG_TOP_K": config_top_k or "",
        "CONFIG_REPETITION_PENALTY": config_repetition_penalty or "",
        "WORKFLOW_NAME": wf_result.get("WORKFLOW_NAME", ""),
        "WORKFLOW_STEP": wf_result.get("WORKFLOW_STEP", ""),
        "WORKFLOW_NEXT": wf_result.get("WORKFLOW_NEXT", ""),
        "PARENT_WORKFLOW": wf_result.get("PARENT_WORKFLOW", ""),
        "PARENT_WORKFLOW_STEP": wf_result.get("PARENT_WORKFLOW_STEP", ""),
        "MATCHED_RULE": matched_rule,
        "SKIP_AGENT": "false",
    }

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

    result = resolve_config(
        trigger_body=trigger_body,
        prompt_base=args.prompt_base,
        workflows_dir=args.workflows_dir,
        is_bot=is_bot,
        repo=args.repo,
        issue_number=args.issue_number,
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
