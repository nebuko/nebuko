#!/usr/bin/env python3
"""
Workflow-aware routing resolver for openhands-multi-agent.yml.

Reads agentRunConfig JSON from stdin, resolves workflow routing, and outputs
environment variables for the GitHub Actions workflow.

Usage:
    python resolve_workflow.py \\
        --workflows-dir workflows/ \\
        --prompt-base .agents/prompts \\
        --agent-config-path agent-config.yaml \\
        <<EOF
    {"task": "genius", "workflow": "default", "step": "analyze"}
    EOF

Output (written to --env-file):
    ROLE_NAME=genius
    PROMPT_LOCATION=.agents/prompts/genius.md
    WORKFLOW_NAME=default
    WORKFLOW_STEP=analyze
    WORKFLOW_NEXT=context
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

import yaml

from config import WorkflowStep, WorkflowDefinition, load_workflow

# Default task-to-prompt mapping (backward compatibility fallback)
TASK_TO_PROMPT = {
    "genius": "genius.md",
    "ctx": "ctx.md",
    "dev": "dev.md",
    "review": "review.md",
    "issue": "issue.md",
    "issue-reviewer": "issue-reviewer.md",
    "summarize": "summarize.md",
    "debug": "debug.md",
}


def resolve_workflow_routing(
    agent_config: dict,
    workflows_dir: str,
    prompt_base: str,
) -> dict:
    """Resolve agent routing based on workflow definition or fallback.

    Args:
        agent_config: Parsed agentRunConfig JSON.
        workflows_dir: Path to workflows/ directory.
        prompt_base: Path to prompt files directory.

    Returns:
        Dict with ROLE_NAME, PROMPT_LOCATION, WORKFLOW_NAME, WORKFLOW_STEP,
        WORKFLOW_NEXT, PARENT_WORKFLOW, PARENT_WORKFLOW_STEP, and SKIP_AGENT.
    """
    task = agent_config.get("task", "")
    workflow_name = agent_config.get("workflow", "")
    step_name = agent_config.get("step", "")
    branch_key = agent_config.get("branch", "")
    parent_workflow = agent_config.get("parent_workflow", "")
    parent_workflow_step = agent_config.get("parent_workflow_step", "")

    # Validate task
    if not task:
        return {"SKIP_AGENT": "true", "REASON": "No task specified"}

    if task not in TASK_TO_PROMPT:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Unknown task: {task}",
        }

    # If workflow is specified, resolve through workflow definition
    if workflow_name:
        return resolve_workflow_step(
            task, workflow_name, step_name, branch_key, workflows_dir, prompt_base,
            parent_workflow, parent_workflow_step,
        )

    # Fallback: direct task-to-agent mapping (backward compatible)
    prompt_file = TASK_TO_PROMPT[task]
    return {
        "ROLE_NAME": task,
        "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
        "WORKFLOW_NAME": "",
        "WORKFLOW_STEP": "",
        "WORKFLOW_NEXT": "",
        "PARENT_WORKFLOW": "",
        "PARENT_WORKFLOW_STEP": "",
        "SKIP_AGENT": "false",
    }


def _is_terminal_step(step: WorkflowStep) -> bool:
    """Check if a step is terminal (no next step, no conditions)."""
    if step.next is None and not step.conditions:
        return True
    if step.next is None and step.conditions:
        # Terminal only if all conditions lead nowhere or there's an explicit null
        for cond in step.conditions:
            if cond.next is not None:
                return False
        return True
    return False


def _resolve_sub_workflow_entry(
    current_step: WorkflowStep,
    workflow: WorkflowDefinition,
    workflows_dir: str,
    prompt_base: str,
) -> Optional[dict]:
    """Resolve entry into a sub-workflow from a step that references another workflow.

    Loads the target workflow and returns the first step's resolution along with
    parent tracking information.
    """
    # Determine target workflow path
    if current_step.path:
        target_path = current_step.path
    elif current_step.workflow:
        target_path = str(Path(workflows_dir) / f"{current_step.workflow}.yaml")
    else:
        return None

    try:
        child_wf = load_workflow(target_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Warning: Failed to load sub-workflow at '{target_path}': {e}",
              file=sys.stderr)
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Sub-workflow not found: {target_path}",
        }

    if not child_wf.steps:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Sub-workflow '{child_wf.name}' has no steps",
        }

    # Resolve to the first step of the child workflow
    first_step = child_wf.steps[0]
    second_step = child_wf.steps[1].name if len(child_wf.steps) > 1 else ""

    prompt_file = TASK_TO_PROMPT.get(first_step.agent)
    if not prompt_file:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Unknown agent '{first_step.agent}' in sub-workflow '{child_wf.name}'",
        }

    return {
        "ROLE_NAME": first_step.agent,
        "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
        "WORKFLOW_NAME": child_wf.name,
        "WORKFLOW_STEP": first_step.name,
        "WORKFLOW_NEXT": second_step,
        "PARENT_WORKFLOW": workflow.name,
        "PARENT_WORKFLOW_STEP": current_step.name,
        "SKIP_AGENT": "false",
    }


def _resolve_return_to_parent(
    parent_workflow_name: str,
    parent_workflow_step_name: str,
    workflows_dir: str,
    prompt_base: str,
) -> Optional[dict]:
    """Resolve returning to parent workflow after sub-workflow completion.

    Loads the parent workflow, finds the step that was the sub-workflow entry point,
    and resolves the next step after it.
    """
    parent_file = Path(workflows_dir) / f"{parent_workflow_name}.yaml"
    if not parent_file.exists():
        print(f"Warning: Parent workflow '{parent_workflow_name}' not found",
              file=sys.stderr)
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Parent workflow '{parent_workflow_name}' not found",
        }

    try:
        parent_wf = load_workflow(str(parent_file))
    except (FileNotFoundError, ValueError) as e:
        print(f"Warning: Failed to load parent workflow: {e}", file=sys.stderr)
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Failed to load parent workflow: {e}",
        }

    # Find the sub-workflow step in the parent
    sub_wf_step = None
    for step in parent_wf.steps:
        if step.name == parent_workflow_step_name:
            sub_wf_step = step
            break

    if sub_wf_step is None:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Step '{parent_workflow_step_name}' not found in parent workflow",
        }

    # Get the next step after the sub-workflow step
    next_step_name = sub_wf_step.next
    if not next_step_name:
        # Sub-workflow step was terminal in parent — workflow ends here
        return {
            "ROLE_NAME": "",
            "PROMPT_LOCATION": "",
            "WORKFLOW_NAME": parent_wf.name,
            "WORKFLOW_STEP": parent_workflow_step_name,
            "WORKFLOW_NEXT": "",
            "PARENT_WORKFLOW": "",
            "PARENT_WORKFLOW_STEP": "",
            "SKIP_AGENT": "true",
            "REASON": "Workflow completed",
        }

    # Find the actual next step
    next_step = None
    for step in parent_wf.steps:
        if step.name == next_step_name:
            next_step = step
            break

    if next_step is None:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Next step '{next_step_name}' not found in parent workflow",
        }

    prompt_file = TASK_TO_PROMPT.get(next_step.agent)
    if not prompt_file:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Unknown agent '{next_step.agent}' in parent workflow",
        }

    return {
        "ROLE_NAME": next_step.agent,
        "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
        "WORKFLOW_NAME": parent_wf.name,
        "WORKFLOW_STEP": next_step_name,
        "WORKFLOW_NEXT": next_step.next or "",
        "PARENT_WORKFLOW": "",
        "PARENT_WORKFLOW_STEP": "",
        "SKIP_AGENT": "false",
    }


def resolve_workflow_step(
    task: str,
    workflow_name: str,
    step_name: str,
    branch_key: str,
    workflows_dir: str,
    prompt_base: str,
    parent_workflow: str = "",
    parent_workflow_step: str = "",
) -> dict:
    """Resolve a specific step within a workflow.

    Handles both normal agent steps and sub-workflow dispatch (entry and return).

    Args:
        task: The agent role from agentRunConfig.
        workflow_name: Name of the workflow (file in workflows/).
        step_name: Current step name within workflow.
        branch_key: Condition key for conditional routing.
        workflows_dir: Path to workflows/ directory.
        prompt_base: Path to prompt files directory.
        parent_workflow: Name of parent workflow (if currently in a sub-workflow).
        parent_workflow_step: Step name in parent that triggered the sub-workflow.

    Returns:
        Dict with routing variables.
    """
    # Check for sub-workflow completion: current step is terminal and we're in a sub-workflow
    # We need to load the current workflow to check if the step is terminal
    workflow_file = Path(workflows_dir) / f"{workflow_name}.yaml"
    current_wf = None
    if workflow_file.exists():
        try:
            current_wf = load_workflow(str(workflow_file))
        except (FileNotFoundError, ValueError):
            pass

    if current_wf:
        current_step = None
        for step in current_wf.steps:
            if step.name == step_name:
                current_step = step
                break

        if current_step and parent_workflow and _is_terminal_step(current_step):
            # Sub-workflow has completed — return control to parent
            return _resolve_return_to_parent(
                parent_workflow, parent_workflow_step, workflows_dir, prompt_base
            )

    # Normal resolution (or non-terminal step)
    if current_wf is None:
        workflow_file = Path(workflows_dir) / f"{workflow_name}.yaml"
        if not workflow_file.exists():
            # Fall back to direct task mapping if workflow file not found
            prompt_file = TASK_TO_PROMPT.get(task)
            if not prompt_file:
                return {"SKIP_AGENT": "true", "REASON": f"Unknown task: {task}"}
            return {
                "ROLE_NAME": task,
                "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
                "WORKFLOW_NAME": workflow_name,
                "WORKFLOW_STEP": "",
                "WORKFLOW_NEXT": "",
                "PARENT_WORKFLOW": parent_workflow,
                "PARENT_WORKFLOW_STEP": parent_workflow_step,
                "SKIP_AGENT": "false",
            }

        try:
            current_wf = load_workflow(str(workflow_file))
        except (FileNotFoundError, ValueError) as e:
            print(f"Warning: Failed to load workflow {workflow_name}: {e}", file=sys.stderr)
            prompt_file = TASK_TO_PROMPT.get(task)
            if not prompt_file:
                return {"SKIP_AGENT": "true", "REASON": f"Unknown task: {task}"}
            return {
                "ROLE_NAME": task,
                "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
                "WORKFLOW_NAME": workflow_name,
                "WORKFLOW_STEP": "",
                "WORKFLOW_NEXT": "",
                "PARENT_WORKFLOW": parent_workflow,
                "PARENT_WORKFLOW_STEP": parent_workflow_step,
                "SKIP_AGENT": "false",
            }

    # Find current step
    if current_step is None:
        if not step_name and current_wf.steps:
            current_step = current_wf.steps[0]
            step_name = current_step.name
        elif current_wf.steps:
            return {
                "SKIP_AGENT": "true",
                "REASON": f"Step '{step_name}' not found in workflow '{workflow_name}'",
            }
        else:
            return {
                "SKIP_AGENT": "true",
                "REASON": f"Workflow {workflow_name} has no steps",
            }

    # Check if this is a sub-workflow entry step
    if current_step.workflow:
        return _resolve_sub_workflow_entry(current_step, current_wf, workflows_dir, prompt_base)

    # Resolve next step
    next_step = None
    if branch_key and current_step.conditions:
        branch_matched = False
        for condition in current_step.conditions:
            if condition.on_result == branch_key:
                next_step = condition.next
                branch_matched = True
                break
        if not branch_matched:
            for condition in current_step.conditions:
                if condition.on_result == "default":
                    next_step = condition.next
                    break
    else:
        next_step = current_step.next

    prompt_file = TASK_TO_PROMPT.get(current_step.agent)
    if not prompt_file:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Unknown agent '{current_step.agent}' in workflow step '{step_name}'",
        }

    return {
        "ROLE_NAME": current_step.agent,
        "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
        "WORKFLOW_NAME": current_wf.name,
        "WORKFLOW_STEP": step_name,
        "WORKFLOW_NEXT": next_step or "",
        "PARENT_WORKFLOW": parent_workflow,
        "PARENT_WORKFLOW_STEP": parent_workflow_step,
        "SKIP_AGENT": "false",
    }


def main():
    parser = argparse.ArgumentParser(description="Resolve workflow routing for agent")
    parser.add_argument(
        "--workflows-dir",
        default="workflows",
        help="Path to workflows directory",
    )
    parser.add_argument(
        "--prompt-base",
        default=".agents/prompts",
        help="Path to prompt files directory",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Path to GITHUB_ENV file to append results",
    )
    parser.add_argument(
        "--agent-config-json",
        default="",
        help="agentRunConfig JSON string (if not reading from stdin)",
    )
    args = parser.parse_args()

    # Read agentRunConfig JSON
    if args.agent_config_json:
        agent_config_str = args.agent_config_json
    else:
        agent_config_str = sys.stdin.read().strip()

    if not agent_config_str:
        print("Error: No agentRunConfig JSON provided", file=sys.stderr)
        sys.exit(1)

    try:
        agent_config = json.loads(agent_config_str)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Resolve routing
    result = resolve_workflow_routing(
        agent_config,
        args.workflows_dir,
        args.prompt_base,
    )

    # Output results
    for key, value in result.items():
        print(f"{key}={value}")

    # If env_file is specified, append to it
    if args.env_file:
        with open(args.env_file, "a") as f:
            for key, value in result.items():
                f.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
