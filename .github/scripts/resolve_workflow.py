#!/usr/bin/env python3
"""
Workflow-aware routing resolver for openhands-multi-agent.yml.

Reads AGENT_RUN_CONFIG JSON from stdin, resolves workflow routing, and outputs
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
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

from config import (
    WorkflowStep,
    WorkflowDefinition,
    load_workflow,
    is_path_like_reference,
    is_valid_workflow_reference,
    resolve_workflow_reference,
)

try:
    from remote_workflow import fetch_remote_workflow
except ImportError:
    fetch_remote_workflow = None  # type: ignore

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
    "po": "po.md",
}


def resolve_workflow_routing(
    agent_config: dict,
    workflows_dir: str,
    prompt_base: str,
) -> dict:
    """Resolve agent routing based on workflow definition or fallback.

    Args:
        agent_config: Parsed AGENT_RUN_CONFIG JSON.
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
    parent_chain = _parse_chain(agent_config.get("parent_chain"))

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
            parent_workflow, parent_workflow_step, parent_chain,
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
        "PARENT_CHAIN": _chain_json(parent_chain),
        "STEP_SKILLS": "",
        "SKIP_AGENT": "false",
    }


def _skills_to_string(skill_list: list[str] | None) -> str:
    """Convert an optional skill list to a comma-separated string."""
    if not skill_list:
        return ""
    return ",".join(skill_list)


def _parse_chain(value: object) -> list[str]:
    """Parse a parent-chain value (JSON array string, list, or missing) safely."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [str(v) for v in parsed]
    return []


def _chain_json(chain: list[str]) -> str:
    """Serialize a parent chain to compact JSON ('[]' when empty)."""
    return json.dumps(list(chain), separators=(",", ":"))


def _match_condition(
    step: WorkflowStep,
    branch_key: str,
) -> Optional[str]:
    """Pick a condition target step name for a branch key.

    Exact-match on ``on_result`` first, then ``default``; None when the
    branch is not covered by the step's conditions (or it has none).
    """
    if not branch_key or not step.conditions:
        return None
    for condition in step.conditions:
        if condition.on_result == branch_key:
            return condition.next
    for condition in step.conditions:
        if condition.on_result == "default":
            return condition.next
    return None


def _branch_key_for(
    step: WorkflowStep,
    branch_key: str,
) -> Optional[str]:
    """Return the effective branch key for a conditional step.

    Exact match wins; an unmatched key falls back to ``"default"`` only when
    a default condition exists.
    """
    if not branch_key or not step.conditions:
        return None
    if _match_condition(step, branch_key) is not None:
        return branch_key
    if any(c.on_result == "default" for c in step.conditions):
        return "default"
    return None


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
    prev_chain: list[str] = (),
    parent_ref: str = "",
) -> Optional[dict]:
    """Resolve entry into a sub-workflow from a step that references another workflow.

    Supports three sources:
    - ``repo`` — remote git repository (cloned & cached locally)
    - ``path`` — explicit filesystem path
    - ``workflow`` — name resolved from ``workflows_dir``

    Loads the target workflow and returns the first step's resolution along with
    parent tracking information.
    """
    # Determine target workflow path
    if current_step.repo:
        if fetch_remote_workflow is None:
            return {
                "SKIP_AGENT": "true",
                "REASON": "remote_workflow module is not available",
            }
        try:
            target_path = fetch_remote_workflow(
                current_step.repo, current_step.ref, current_step.workflow_name
            )
        except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"Warning: Failed to fetch remote workflow: {e}", file=sys.stderr)
            return {
                "SKIP_AGENT": "true",
                "REASON": f"Failed to fetch remote workflow: {e}",
            }
    elif current_step.path:
        target_path = current_step.path
    elif current_step.workflow:
        # ``workflow`` field may be a name OR a repo-root-relative path (#468)
        target_path = str(
            resolve_workflow_reference(current_step.workflow, workflows_dir)
        )
    else:
        return None

    try:
        child_wf = load_workflow(target_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Warning: Failed to load sub-workflow at '{target_path}': {e}",
              file=sys.stderr)
        # For dynamically mounted plan workflows (issue #470), propagate the
        # actionable message from load_workflow (e.g. the scaffold hint).
        normalized = str(target_path).replace(os.sep, "/")
        if normalized.startswith("tmp_impl/"):
            return {
                "SKIP_AGENT": "true",
                "REASON": str(e),
            }
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

    # Reloadable reference of the parent for the return leg (issue #470
    # Bug B/D): the ``workflow:``/``path:`` field as written, never the
    # loaded definition's ``name:`` — path-mounted parents (e.g.
    # tmp_impl/workflow.yaml) cannot be reloaded by their def name.
    effective_parent_ref = parent_ref or workflow.name
    return {
        "ROLE_NAME": first_step.agent,
        "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
        "WORKFLOW_NAME": child_wf.name,
        "WORKFLOW_STEP": first_step.name,
        "WORKFLOW_NEXT": second_step,
        "PARENT_WORKFLOW": effective_parent_ref,
        "PARENT_WORKFLOW_STEP": current_step.name,
        "PARENT_CHAIN": _chain_json([effective_parent_ref, *prev_chain]),
        "STEP_SKILLS": _skills_to_string(first_step.skill),
        "SKIP_AGENT": "false",
    }


def _resolve_return_to_parent(
    parent_workflow_name: str,
    parent_workflow_step_name: str,
    branch_key: str,
    workflows_dir: str,
    prompt_base: str,
    prev_chain: list[str] = (),
) -> Optional[dict]:
    """Resolve returning to parent workflow after sub-workflow completion.

    Loads the parent workflow, finds the step that was the sub-workflow entry point,
    and resolves the next step after it using conditional routing if available.

    ``prev_chain`` is the ancestor chain recorded while inside the finished
    sub-workflow; the returned ``PARENT_CHAIN`` pops its first element (the
    finished child's parent) so the resumed state carries the remaining
    ancestors (issue #470 multi-tier nesting).
    """
    # Load parent workflow — supports names and repo-root-relative paths
    # (issue #468): path-like references resolve outside workflows/.
    if is_path_like_reference(parent_workflow_name):
        resolved = resolve_workflow_reference(parent_workflow_name, workflows_dir)
    else:
        resolved = Path(workflows_dir) / f"{parent_workflow_name}.yaml"
    if not Path(resolved).exists():
        print(f"Warning: Parent workflow '{parent_workflow_name}' not found",
              file=sys.stderr)
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Parent workflow '{parent_workflow_name}' not found",
        }

    try:
        parent_wf = load_workflow(str(resolved))
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

    # Resolve next step — support conditional routing
    next_step_name = None
    if branch_key and sub_wf_step.conditions:
        branch_matched = False
        for condition in sub_wf_step.conditions:
            if condition.on_result == branch_key:
                next_step_name = condition.next
                branch_matched = True
                break
        if not branch_matched:
            for condition in sub_wf_step.conditions:
                if condition.on_result == "default":
                    next_step_name = condition.next
                    break
    else:
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
            "PARENT_CHAIN": _chain_json(list(prev_chain)[1:]),
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
        "PARENT_CHAIN": _chain_json(list(prev_chain)[1:]),
        "STEP_SKILLS": _skills_to_string(next_step.skill),
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
    parent_chain: Optional[list[str]] = None,
) -> dict:
    """Resolve a specific step within a workflow.

    Handles both normal agent steps and sub-workflow dispatch (entry and return).

    Args:
        task: The agent role from AGENT_RUN_CONFIG.
        workflow_name: Name of the workflow (file in workflows/).
        step_name: Current step name within workflow.
        branch_key: Condition key for conditional routing.
        workflows_dir: Path to workflows/ directory.
        prompt_base: Path to prompt files directory.
        parent_workflow: Name of parent workflow (if currently in a sub-workflow).
        parent_workflow_step: Step name in parent that triggered the sub-workflow.
        parent_chain: Ordered ancestor chain of reloadable workflow references
            (newest-first) carried across nested sub-workflows (issue #470).

    Returns:
        Dict with routing variables.
    """
    # Load workflow definition — supports names and repo-root-relative paths
    # (issue #468): path-like references resolve outside workflows/.
    if is_path_like_reference(workflow_name):
        resolved = resolve_workflow_reference(workflow_name, workflows_dir)
    else:
        resolved = Path(workflows_dir) / f"{workflow_name}.yaml"
    if not Path(resolved).exists():
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
            "PARENT_CHAIN": _chain_json(list(parent_chain or [])),
            "STEP_SKILLS": "",
            "SKIP_AGENT": "false",
        }

    try:
        current_wf = load_workflow(str(resolved))
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
            "PARENT_CHAIN": _chain_json(list(parent_chain or [])),
            "STEP_SKILLS": "",
            "SKIP_AGENT": "false",
        }

    # Find current step
    current_step = None
    for step in current_wf.steps:
        if step.name == step_name:
            current_step = step
            break

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

    # Check for sub-workflow completion: terminal step and we're in a sub-workflow
    if parent_workflow and _is_terminal_step(current_step):
        return _resolve_return_to_parent(
            parent_workflow, parent_workflow_step, branch_key, workflows_dir, prompt_base,
            prev_chain=list(parent_chain or []),
        )

    # Check if this is a sub-workflow entry step (local or remote)
    if current_step.workflow or current_step.repo:
        # ``workflow_name`` is the reloadable parent reference (name or
        # repo-root-relative path, issue #470 Bug B/D) for the return leg.
        return _resolve_sub_workflow_entry(
            current_step, current_wf, workflows_dir, prompt_base,
            prev_chain=list(parent_chain or []),
            parent_ref=workflow_name,
        )

    # Resolve next step
    next_step: Optional[str] = None
    if branch_key and current_step.conditions:
        target = _match_condition(current_step, branch_key)
        if target is not None:
            next_step = target
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
        "PARENT_CHAIN": _chain_json(list(parent_chain or [])),
        "STEP_SKILLS": _skills_to_string(current_step.skill),
        "SKIP_AGENT": "false",
    }


def _load_workflow_by_reference(
    workflow_name: str,
    workflows_dir: str,
) -> Optional[WorkflowDefinition]:
    """Load a workflow by name or repo-root-relative path reference."""
    if not is_valid_workflow_reference(workflow_name):
        return None
    reference = resolve_workflow_reference(workflow_name, workflows_dir)
    try:
        return load_workflow(str(reference))
    except (FileNotFoundError, ValueError):
        return None


def _resolve_sub_workflow_ref_target(
    current_step: WorkflowStep,
) -> Optional[str]:
    """Resolve the target workflow reference for a sub-workflow entry step.

    Returns the effective reference (child workflow name or path), or None
    when the step references no local sub-workflow.
    """
    if current_step.path:
        return current_step.path
    if current_step.workflow:
        return current_step.workflow
    return None


def _ref_step_targeting(
    workflow: WorkflowDefinition,
    target_reference: str,
) -> Optional[WorkflowStep]:
    """Find the sub-workflow reference step in ``workflow`` that mounts
    ``target_reference`` (its ``path`` or ``workflow`` field). Used to locate
    an ancestor's resume point during multi-tier bubble-out (issue #470),
    since the persisted chain holds reloadable refs, not step names.
    """
    for step in workflow.steps:
        ref = _resolve_sub_workflow_ref_target(step)
        if ref == target_reference:
            return step
    return None


def _effective_entry_step(
    child_workflow: WorkflowDefinition,
    workflows_dir: str,
    max_depth: int = 5,
) -> Optional[WorkflowStep]:
    """Descend nested sub-workflow references to the effective entry step.

    A dynamically mounted plan workflow (issue #470) may have steps that are
    themselves ``workflow:``/``path:`` references (e.g. a scaffold-generated
    ``tmp_impl/workflow.yaml`` whose steps reference ``dev-review-loop``) and
    carry no ``agent``. Walks from ``steps[0]`` through such references until
    reaching a step with a real agent; returns the deepest step reached when
    a chain cannot be followed (load failure or depth cap).

    Remote (``repo``) sources are out of scope for the trigger-side resolver
    and stop the descent. Returns None when the workflow has no steps.
    """
    if not child_workflow.steps:
        return None
    step = child_workflow.steps[0]
    for _ in range(max_depth):
        ref = _resolve_sub_workflow_ref_target(step)
        if not ref:
            return step
        child = _load_workflow_by_reference(ref, workflows_dir)
        if child is None or not child.steps:
            return step
        step = child.steps[0]
    return step


def _child_entry_resolution(
    child_workflow: WorkflowDefinition,
    child_reference: str,
    prompt_base: str,
) -> Optional[dict]:
    """Resolve a step's agent+prompt for entering ``child_workflow``.

    Returns None when the child has no steps or its first agent is unknown.
    """
    if not child_workflow.steps:
        return None
    first_step = child_workflow.steps[0]
    second_step = child_workflow.steps[1].name if len(child_workflow.steps) > 1 else ""
    prompt_file = TASK_TO_PROMPT.get(first_step.agent)
    if not prompt_file:
        return None
    return {
        "ROLE_NAME": first_step.agent,
        "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
        "WORKFLOW_NAME": child_workflow.name,
        "WORKFLOW_STEP": first_step.name,
        "WORKFLOW_NEXT": second_step,
        "STEP_SKILLS": _skills_to_string(first_step.skill),
        "SKIP_AGENT": "false",
    }


def _sub_workflow_entry_marker(
    child_workflow: WorkflowDefinition,
    child_reference: str,
    parent_reference: str,
    parent_step: WorkflowStep,
    prev_chain: list[str],
    workflows_dir: str,
) -> dict:
    """Build the 'enter sub-workflow' result structure.

    Carries the sub-workflow's first step plus parent tracking so the caller
    can record ``parent_workflow`` / ``parent_workflow_step``.

    ``parent_reference`` MUST be the reloadable reference of the parent
    (name or repo-root-relative path, issue #470 Bug B/D), never the loaded
    definition's ``name:`` field — the return leg reloads it via
    ``_load_workflow_by_reference``.

    ``task`` is the agent of the EFFECTIVE entry step: ``steps[0]`` may itself
    be a nested ``workflow:`` reference with no agent (issue #470, e.g.
    scaffold-generated ``tmp_impl/workflow.yaml`` → ``dev-review-loop``).
    ``step``/``workflow`` stay at the immediate child so the env path
    (``resolve_workflow_step``) re-descends from a step name that matches the
    child file; single-level entries are unchanged (descent returns ``steps[0]``).
    """
    first_step = child_workflow.steps[0]
    entry_step = _effective_entry_step(child_workflow, workflows_dir) or first_step
    return {
        "entered_sub_workflow": True,
        "task": entry_step.agent,
        "step": first_step.name,
        "workflow": child_reference,
        "parent_workflow": parent_reference,
        "parent_workflow_step": parent_step.name,
        "parent_chain": [parent_reference, *prev_chain],
    }


def _return_to_parent_marker(
    parent_workflow: WorkflowDefinition,
    parent_step_name: str,
    branch_key: Optional[str],
    parent_reference: str,
    parent_workflows_dir: str = "",
    parent_chain: Optional[list[str]] = None,
) -> Optional[dict]:
    """Build the 'exit sub-workflow' result structure.

    Loads nothing for the immediate parent; given the parent's sub-workflow
    reference step, resolves the parent's next step after it, honoring that
    step's conditions against the incoming branch key. ``parent_reference``
    is the reloadable parent reference (name or path) restored as
    ``config_workflow``.

    Bug C (issue #470): if the resolved target step is ITSELF a sub-workflow
    reference, chain into it (recursive entry) so a nested exit can bubble
    more than one tier. The returned marker carries ``parent_chain`` — the
    NEW chain after the transition (popped parent, re-prepended child when
    chaining deeper). Returns None when the parent step is missing or
    itself terminal (overall terminal).
    """
    parent_step = next(
        (s for s in parent_workflow.steps if s.name == parent_step_name), None
    )
    if parent_step is None:
        return None

    target_name = _match_condition(parent_step, branch_key or "")
    if target_name is None:
        target_name = parent_step.next
    if target_name is None:
        return None

    target_step = next(
        (s for s in parent_workflow.steps if s.name == target_name), None
    )
    if target_step is None:
        return None

    # Chain into a nested sub-workflow target (Bug C multi-tier bubbling).
    # The resume point in the parent is the TARGET step (the sub-workflow
    # ref just entered), not the incoming ref step. Ancestor chain keeps
    # the direct parent innermost (chain holds ancestors only).
    child_ref = _resolve_sub_workflow_ref_target(target_step)
    if child_ref:
        child_workflow = _load_workflow_by_reference(child_ref, parent_workflows_dir)
        if child_workflow is not None and child_workflow.steps:
            entry = _effective_entry_step(child_workflow, parent_workflows_dir) or child_workflow.steps[0]
            second = (
                child_workflow.steps[1].name
                if len(child_workflow.steps) > 1 else ""
            )
            grandparent_refs = (
                list(parent_chain[1:])
                if parent_chain and parent_chain[0] == parent_reference
                else list(parent_chain or [])
            )
            return {
                "entered_sub_workflow": True,
                "task": entry.agent,
                "step": entry.name,
                "workflow": child_ref,
                "next_step": second,
                "parent_workflow": parent_reference,
                "parent_workflow_step": target_step.name,
                "parent_chain": [parent_reference, *grandparent_refs],
            }

    marker: dict = {
        "exited_sub_workflow": True,
        "task": target_step.agent,
        "step": target_step.name,
        "workflow": parent_reference,
        "parent_chain": list((parent_chain or [])[1:]),
    }
    effective_branch = _branch_key_for(parent_step, branch_key or "")
    if effective_branch:
        marker["branch"] = effective_branch
    return marker


def _return_to_parent_on_terminal(
    parent_workflow: str,
    parent_workflow_step: str,
    branch_result: Optional[str],
    workflows_dir: str,
    agent_hint: Optional[str],
    parent_chain: Optional[list[str]] = None,
) -> Optional[dict]:
    """Exit to the parent when a sub-workflow cannot advance internally.

    Used when the current step is terminal (``next: null``) or its target
    step is missing. Returns the parent's next step after the sub-workflow
    reference (chaining into a further-nested target when it is itself a
    sub-workflow ref — Bug C), or falls back to ``agent_hint`` / ``None``
    when no parent is set (overall terminal).

    Multi-tier bubbling: when the resolved parent step is itself terminal
    (e.g. the last per-step ref in a dynamically mounted plan workflow),
    the chain pops and the bubble continues to the next ancestor.
    """
    if parent_workflow and parent_workflow_step:
        refs = list(parent_chain or [])
        # Innermost ancestors first; the immediate parent (index 0) is
        # already passed in as parent_workflow/parent_workflow_step. Deeper
        # ancestors only persist their reloadable ref — locate their resume
        # step by finding the ref step that mounts the tier just exited.
        order = [(parent_workflow, parent_workflow_step)] + [
            (ref, None) for ref in refs[1:]
        ]
        for i, (ref, step_name) in enumerate(order):
            if not ref:
                continue
            if step_name is None and i > 0:
                inner_ref = order[i - 1][0]
                loaded = _load_workflow_by_reference(ref, workflows_dir)
                if loaded is None:
                    continue
                mounting = _ref_step_targeting(loaded, inner_ref)
                if mounting is None:
                    continue
                step_name = mounting.name
            parent_wf = _load_workflow_by_reference(ref, workflows_dir)
            if parent_wf is None:
                continue
            marker = _return_to_parent_marker(
                parent_wf,
                step_name,
                branch_result,
                ref,
                workflows_dir,
                refs[i:],
            )
            if marker is not None:
                return marker
    if agent_hint:
        return {"task": agent_hint}
    return None


def resolve_next_from_workflow(
    workflow_name: str,
    current_step_name: str,
    workflows_dir: str,
    agent_hint: Optional[str] = None,
    branch_result: Optional[str] = None,
    parent_workflow: str = "",
    parent_workflow_step: str = "",
    parent_chain: Optional[list[str]] = None,
) -> Optional[dict]:
    """Resolve the next step in a workflow given the current step.

    ``workflow_name`` may be a workflow name (resolved from ``workflows_dir``)
    or a repo-root-relative path (issue #468) — same resolution rules as
    ``load_workflow``.

    Handles the sub-workflow lifecycle:
    - A current step that references a sub-workflow (``workflow``/``path``
      field) yields an entry marker (``entered_sub_workflow``) with the
      child's first step and the parent tracking fields.
    - A terminal current step with ``parent_workflow`` set yields an exit
      marker (``exited_sub_workflow``) pointing at the parent's next step
      after the sub-workflow reference, or ``None`` when the parent step is
      terminal too (overall terminal).

    Args:
        workflow_name: Workflow name or repo-root-relative path.
        current_step_name: Name of the current step.
        workflows_dir: Path to the workflows directory.
        agent_hint: Fallback agent name when no workflow resolution is found.
        branch_result: Condition key for conditional routing (e.g. "approved").
        parent_workflow: Parent workflow name/ref when currently inside a
            sub-workflow; enables return-to-parent on terminal steps.
        parent_workflow_step: The parent's sub-workflow reference step name;
            the parent resumes at the step after this one.

    Returns:
        ``{"task", "step"}`` for linear routing, ``{"task", "step", "branch"}``
        for conditional branches, ``{"task": "<hint>"}`` for agent hint
        fallback, entry/exit markers for sub-workflow transitions, or
        ``None`` for terminal/missing cases.
    """
    workflow = _load_workflow_by_reference(workflow_name, workflows_dir)
    if workflow is None:
        if agent_hint:
            return {"task": agent_hint}
        return None

    # Find current step
    current_step = None
    for step in workflow.steps:
        if step.name == current_step_name:
            current_step = step
            break

    if current_step is None:
        if agent_hint:
            return {"task": agent_hint}
        return None

    # Sub-workflow entry step (local ref only; remote sources are out of
    # scope for the trigger-side resolver). ``workflow_name`` is the
    # reloadable reference of the current workflow — record IT (Bug B/D),
    # not the loaded definition's name.
    child_ref = _resolve_sub_workflow_ref_target(current_step)
    if child_ref:
        child_workflow = _load_workflow_by_reference(child_ref, workflows_dir)
        if child_workflow is not None and child_workflow.steps:
            return _sub_workflow_entry_marker(
                child_workflow, child_ref, workflow_name, current_step,
                parent_chain or [], workflows_dir,
            )

    # Terminal step: return to parent when inside a sub-workflow
    if _is_terminal_step(current_step):
        if parent_workflow and parent_workflow_step:
            marker = _return_to_parent_on_terminal(
                parent_workflow,
                parent_workflow_step,
                branch_result,
                workflows_dir,
                agent_hint,
                parent_chain,
            )
            if marker is not None:
                return marker
        if agent_hint:
            return {"task": agent_hint}
        return None

    # Resolve target step name
    target_step_name: Optional[str] = None
    branch_key: Optional[str] = None

    if branch_result and current_step.conditions:
        # Try to match a specific condition; a match on ``next: null`` is
        # terminal (sub-workflow exit) and must NOT fall through to default.
        matched = False
        for condition in current_step.conditions:
            if condition.on_result == branch_result:
                target_step_name = condition.next
                branch_key = branch_result
                matched = True
                break
        # Fall back to default condition only when nothing matched
        if not matched:
            for condition in current_step.conditions:
                if condition.on_result == "default":
                    target_step_name = condition.next
                    branch_key = "default"
                    break
    else:
        # Follow linear next field
        target_step_name = current_step.next

    if target_step_name is None:
        return _return_to_parent_on_terminal(
            parent_workflow, parent_workflow_step, branch_result,
            workflows_dir, agent_hint, parent_chain,
        )

    # Look up target step to get its agent
    target_step = None
    for step in workflow.steps:
        if step.name == target_step_name:
            target_step = step
            break

    if target_step is None:
        # Broken next reference: fall back to the parent return leg (when
        # inside a sub-workflow) so a dangling ref can't strand the run;
        # otherwise resolution fails cleanly (no silent hint fallback).
        marker = _return_to_parent_on_terminal(
            parent_workflow, parent_workflow_step, branch_key,
            workflows_dir, agent_hint, parent_chain,
        )
        if marker is not None:
            return marker
        return None

    # Next round is a sub-workflow entry (chain, Bug C): record the
    # reloadable reference of the current workflow as the new parent and
    # grow the ancestor chain.
    child_ref = _resolve_sub_workflow_ref_target(target_step)
    if child_ref:
        child_workflow = _load_workflow_by_reference(child_ref, workflows_dir)
        if child_workflow is not None and child_workflow.steps:
            return _sub_workflow_entry_marker(
                child_workflow, child_ref, workflow_name, target_step,
                parent_chain or [], workflows_dir,
            )

    result: dict = {
        "task": target_step.agent,
        "step": target_step.name,
        "workflow": workflow_name,
        "parent_chain": list(parent_chain or []),
    }
    if branch_key:
        result["branch"] = branch_key
    return result


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
        help="AGENT_RUN_CONFIG JSON string (if not reading from stdin)",
    )
    args = parser.parse_args()

    # Read AGENT_RUN_CONFIG JSON
    if args.agent_config_json:
        agent_config_str = args.agent_config_json
    else:
        agent_config_str = sys.stdin.read().strip()

    if not agent_config_str:
        print("Error: No AGENT_RUN_CONFIG JSON provided", file=sys.stderr)
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
