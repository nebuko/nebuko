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
    load_workflow_with_dirs,
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
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
) -> dict:
    """Resolve agent routing based on workflow definition or fallback.

    Args:
        agent_config: Parsed AGENT_RUN_CONFIG JSON.
        workflows_dir: Path to workflows/ directory.
        prompt_base: Path to prompt files directory.
        local_workflows_dir: Optional consumer-local workflows dir (issue #529).
        scaffold_branch: Branch carrying a branch-mounted child workflow;
            enables fetch-on-miss for children absent from the local checkout
            (issue #604).

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
            local_workflows_dir=local_workflows_dir,
            scaffold_branch=scaffold_branch,
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
        "STEP_SKILLS": "",
        "STEP_PROMPT_FILE": "",
        "SKIP_AGENT": "false",
    }


def _skills_to_string(skill_list: list[str] | None) -> str:
    """Convert an optional skill list to a comma-separated string."""
    if not skill_list:
        return ""
    return ",".join(skill_list)


def _write_step_prompt_file(prompt: str | None) -> str:
    """Write a non-empty step prompt to a temp file; return the path or ''.

    Prompts contain newlines, which would corrupt the line-based KEY=value
    RESOLVE_OUTPUT protocol, so the content is written to a file and only
    the path is emitted (issue #551, same pattern as PROMPT_LOCATION).
    """
    if not prompt or not prompt.strip():
        # Remove any stale file from a previous resolution on this runner.
        path = "/tmp/step_prompt.md"
        if os.path.exists(path):
            os.remove(path)
        return ""
    path = "/tmp/step_prompt.md"
    with open(path, "w") as f:
        f.write(prompt)
    return path


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


def _scaffold_child_reference(current_step: WorkflowStep) -> Optional[str]:
    """Repo-root-relative reference for a branch-mounted child (issue #604).

    Returns the step's raw ``path``/``workflow`` reference (as the author
    wrote it) so the fetcher can locate the file inside the branch checkout,
    or ``None`` when the step has no fetchable local reference (e.g. a
    ``repo`` step, which is fetched elsewhere).
    """
    if current_step.path:
        return current_step.path
    if current_step.workflow:
        return current_step.workflow
    return None


def _origin_repo_url() -> Optional[str]:
    """Return the ``origin`` remote URL of the current checkout, or None.

    In reusable-workflow mode the runner checks out the user's repo as the
    primary working tree, so its ``origin`` is the correct fetch source for
    a branch-mounted child (issue #604). Never raises.
    """
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _scaffold_fetch(name: str, scaffold_branch: Optional[str]) -> Optional[str]:
    """Best-effort fetch of a branch-mounted workflow file (issue #604).

    Returns an absolute path to the fetched workflow file, or ``None`` when
    the fetch cannot be attempted or fails. Never raises: the miss is
    surfaced by the caller (clean skip reason or the task fallback).
    """
    if not scaffold_branch or fetch_remote_workflow is None:
        return None
    origin = _origin_repo_url()
    if not origin:
        return None
    try:
        return fetch_remote_workflow(origin, scaffold_branch, name)
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


def _fetch_scaffold_child(
    current_step: WorkflowStep, scaffold_branch: Optional[str]
) -> Optional[str]:
    """Fetch a branch-mounted child workflow (sub-workflow entry, issue #604)."""
    ref = _scaffold_child_reference(current_step)
    if not ref:
        return None
    return _scaffold_fetch(ref, scaffold_branch)


def _parent_step_child_reference(
    parent_workflow: str,
    parent_step_name: str,
    workflows_dir: str,
) -> Optional[str]:
    """Child-workflow reference declared by a parent workflow step (issue #604).

    The parent workflow lives in the default-branch checkout (e.g.
    ``plan-orchestration.yaml``), so its step declaration (e.g.
    ``workflow: tmp_impl/workflow.yaml``) is the authoritative reference for
    where the branch-mounted child file lives. ``None`` when the parent or
    step cannot be resolved locally.
    """
    if is_path_like_reference(parent_workflow):
        resolved = resolve_workflow_reference(parent_workflow, workflows_dir)
    else:
        resolved = Path(workflows_dir) / f"{parent_workflow}.yaml"
    if not Path(resolved).exists():
        return None
    try:
        parent_wf = load_workflow(str(resolved))
    except (FileNotFoundError, ValueError):
        return None
    for step in parent_wf.steps:
        if step.name == parent_step_name:
            return _scaffold_child_reference(step)
    return None


def _fetch_scaffold_workflow_file(
    name: str,
    workflows_dir: str,
    scaffold_branch: Optional[str],
    parent_workflow: str = "",
    parent_workflow_step: str = "",
) -> Optional[str]:
    """Fetch a branch-mounted workflow file at step-entry re-resolution.

    ``resolve_workflow_step`` loads the *current* workflow by name; a
    branch-mounted child (e.g. the generated ``tmp_impl/workflow.yaml``) has
    a name (e.g. ``implchild``) that does not match its file path, so the
    fetch is driven by the child reference the parent step declared, with
    the raw name and a ``.yaml`` suffix as fallbacks. ``None`` when the
    fetch cannot be attempted or fails.
    """
    if not scaffold_branch or is_path_like_reference(name):
        return None
    if (Path(workflows_dir) / f"{name}.yaml").exists():
        return None  # local hit wins; no fetch
    refs = [name, f"{name}.yaml"] if not name.endswith(".yaml") else [name]
    if parent_workflow and parent_workflow_step:
        declared = _parent_step_child_reference(
            parent_workflow, parent_workflow_step, workflows_dir
        )
        if declared:
            refs = [declared]
    for ref in refs:
        path = _scaffold_fetch(ref, scaffold_branch)
        if path:
            return path
    return None


def _sub_workflow_miss_reason(
    target_path: str, scaffold_branch: Optional[str]
) -> dict:
    """Clean SKIP_AGENT result for a child workflow that could not be loaded.

    The reason names what was tried so a user can tell whether the branch was
    absent, the state lacked ``scaffold_branch``, or the fetch failed (issue
    #604). Always contains ``Sub-workflow not found``.
    """
    if scaffold_branch:
        detail = f"default-branch checkout, scaffold_branch {scaffold_branch}; fetch failed"
    else:
        detail = "default-branch checkout (no scaffold_branch in state)"
    return {
        "SKIP_AGENT": "true",
        "REASON": f"Sub-workflow not found: {target_path} (tried: {detail})",
    }


def _load_child_workflow(
    ref: str,
    workflows_dir: str,
    scaffold_branch: Optional[str],
    fetch_cache: Optional[dict],
    local_workflows_dir: Optional[str] = None,
) -> Optional[WorkflowDefinition]:
    """Load a sub-workflow by name or repo-root-relative path (issue #609).

    Tries the local (default-branch) checkout first; on a miss and a
    ``scaffold_branch`` set, fetches the file from that branch (issue #604).
    A per-entry ``fetch_cache`` (ref -> path) avoids re-fetching a branch-mounted
    child more than once during a single nested descent. Returns ``None`` when
    the workflow cannot be located or loaded. Plain names honor the
    consumer-local directory precedence (issue #529) when
    ``local_workflows_dir`` is given.
    """
    if not is_path_like_reference(ref) and local_workflows_dir:
        try:
            resolved = str(load_workflow_with_dirs(ref, workflows_dir, local_workflows_dir))
        except (FileNotFoundError, ValueError):
            resolved = ""
    else:
        resolved = ""
    if not resolved:
        resolved = str(resolve_workflow_reference(ref, workflows_dir))
    if Path(resolved).exists():
        try:
            return load_workflow(resolved)
        except (FileNotFoundError, ValueError) as e:
            print(f"Warning: Failed to load sub-workflow '{resolved}': {e}", file=sys.stderr)
            return None
    fetched = None
    if scaffold_branch:
        if fetch_cache is not None and ref in fetch_cache:
            fetched = fetch_cache[ref]
        else:
            fetched = _scaffold_fetch(ref, scaffold_branch)
            if fetched and fetch_cache is not None:
                fetch_cache[ref] = fetched
    if fetched:
        try:
            return load_workflow(fetched)
        except (FileNotFoundError, ValueError) as e:
            print(f"Warning: Fetched sub-workflow '{fetched}' is unloadable: {e}", file=sys.stderr)
            return None
    print(f"Warning: Sub-workflow '{ref}' not found", file=sys.stderr)
    return None


def _resolve_entry_leaf_for_step(
    step: WorkflowStep,
    workflows_dir: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve the runnable leaf a sub-workflow entry step descends to.

    Shared by the runtime entry path and the trigger-side marker builders
    (issue #609) so all entry routes agree. Loads the referenced child and
    descends to the innermost runnable leaf. Returns ``{"task", "step",
    "workflow"}`` on success (``task`` empty for script leaves) or
    ``{"error": <reason>}``.
    """
    ref = step.workflow or step.path
    if not ref:
        return {"error": f"step '{step.name}' has no sub-workflow reference"}
    child = _load_child_workflow(
        ref, workflows_dir, scaffold_branch,
        local_workflows_dir=local_workflows_dir, fetch_cache={},
    )
    if child is None:
        return {
            "error": f"Sub-workflow '{ref}' not found (entered via step '{step.name}')"
        }
    leaf = _resolve_sub_workflow_leaf(
        child, step, workflows_dir, scaffold_branch, {}, {child.name},
        parent_skill=step.skill,
    )
    if leaf["kind"] == "error":
        return {"error": leaf["error"]}
    return {
        "task": leaf["agent"] if leaf["kind"] == "agent" else "",
        "step": leaf["step"],
        "workflow": leaf["workflow"],
    }


def _resolve_sub_workflow_leaf(
    child_wf: WorkflowDefinition,
    entry_step: WorkflowStep,
    workflows_dir: str,
    scaffold_branch: Optional[str],
    fetch_cache: dict,
    visited: set,
    parent_skill: Optional[list] = None,
) -> dict:
    """Resolve the innermost runnable leaf of a (possibly nested) child.

    Descends through child first-steps that are themselves ``workflow:``/
    ``path:`` sub-workflow references until a runnable leaf is found (an
    ``agent`` step, or a deterministic ``script`` step). A child whose first
    step has no runnable source is a leaf in its own right and is surfaced as
    an error (issue #609). A visited-reference set bounds the descent against
    circular references.

    Returns a dict with:
    - ``kind``: "agent" | "script" | "error"
    - ``error``: reason string (only when kind == "error")
    - ``agent``/``step``/``workflow``/``workflow_next``: leaf step details
      (agent/script)
    - ``skill``/``prompt``: merged skills + step prompt (parent entry-step
      skills take precedence over the leaf step's own skills)
    """
    wf = child_wf
    leaf = wf.steps[0] if wf.steps else None
    while leaf is not None and not leaf.agent and leaf.script is None:
        # A first-step with no runnable source is itself a leaf: surface it.
        if not leaf.workflow and not leaf.path and not leaf.repo:
            break
        ref = leaf.workflow or leaf.path
        if not ref:
            break
        if ref in visited:
            return {
                "kind": "error",
                "error": (
                    f"sub-workflow reference cycle detected while entering "
                    f"'{entry_step.workflow or entry_step.path}' "
                    f"(visited: {', '.join(sorted(visited))})"
                ),
            }
        visited.add(ref)
        child = _load_child_workflow(
            ref, workflows_dir, scaffold_branch, fetch_cache
        )
        if child is None:
            return {
                "kind": "error",
                "error": (
                    f"Sub-workflow not found: {ref} "
                    f"(nested under '{entry_step.workflow or entry_step.path}')"
                ),
            }
        # Carry the first declared skill down through the descent: a
        # per-plan skill (issue #609) may sit on an intermediate
        # reference step rather than the original entry step.
        if parent_skill is None and leaf.skill:
            parent_skill = leaf.skill
        wf = child
        leaf = child.steps[0] if child.steps else None
        if leaf is None:
            return {
                "kind": "error",
                "error": f"Sub-workflow '{child.name}' has no steps",
            }

    if leaf.script is not None:
        return {
            "kind": "script",
            "step": leaf.name,
            "workflow": wf.name,
            "script_path": leaf.script.path,
            "script_args": " ".join(leaf.script.args or []),
            "script_timeout": leaf.script.timeout_minutes,
            "skill": _skills_to_string(parent_skill or leaf.skill),
            "prompt": leaf.prompt,
        }
    if leaf.agent:
        return {
            "kind": "agent",
            "agent": leaf.agent,
            "step": leaf.name,
            "workflow": wf.name,
            "workflow_next": wf.steps[1].name if len(wf.steps) > 1 else "",
            "skill": _skills_to_string(parent_skill or leaf.skill),
            "prompt": leaf.prompt,
        }
    # Runnable-source-less leaf (e.g. a first-step with only a ``repo`` ref):
    # surface a precise reason instead of "Unknown agent 'None'" (issue #609).
    return {
        "kind": "error",
        "error": (
            f"Unknown agent '{leaf.agent}' in sub-workflow '{wf.name}' "
            f"(step '{leaf.name}'; entered via "
            f"'{entry_step.workflow or entry_step.path}')"
        ),
    }


def _resolve_sub_workflow_entry(
    current_step: WorkflowStep,
    workflow: WorkflowDefinition,
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
) -> Optional[dict]:
    """Resolve entry into a sub-workflow from a step that references another workflow.

    Supports three sources:
    - ``repo`` — remote git repository (cloned & cached locally)
    - ``path`` — explicit filesystem path
    - ``workflow`` — name resolved from ``workflows_dir``

    Loads the target workflow and returns the first step's resolution along with
    parent tracking information.

    When the child file is absent from the local (default-branch) checkout and
    ``scaffold_branch`` is set, the file is fetched from that branch via the
    origin remote (issue #604) — this is how a branch-mounted child such as
    ``tmp_impl/workflow.yaml`` resolves.
    """
    # Determine target workflow path
    fetch_cache: dict = {}
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
        fetch_cache["__repo__"] = target_path
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
        # Fetch-on-miss (issue #604): a branch-mounted child (e.g.
        # ``tmp_impl/workflow.yaml``) is absent from the default-branch
        # checkout; fetch it from the state's ``scaffold_branch``.
        fetched = _fetch_scaffold_child(current_step, scaffold_branch)
        if fetched:
            try:
                child_wf = load_workflow(fetched)
                target_path = fetched
            except (FileNotFoundError, ValueError) as e2:
                print(f"Warning: Fetched sub-workflow at '{fetched}' is unloadable: {e2}",
                      file=sys.stderr)
                return _sub_workflow_miss_reason(target_path, scaffold_branch)
        else:
            print(f"Warning: Failed to load sub-workflow at '{target_path}': {e}",
                  file=sys.stderr)
            return _sub_workflow_miss_reason(target_path, scaffold_branch)

    if not child_wf.steps:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Sub-workflow '{child_wf.name}' has no steps",
        }

    # Descend to the innermost runnable leaf (issue #609): the child's first
    # step may itself reference a (further nested) sub-workflow.
    leaf = _resolve_sub_workflow_leaf(
        child_wf, current_step, workflows_dir, scaffold_branch,
        fetch_cache, {child_wf.name},
        parent_skill=current_step.skill,
    )
    if leaf["kind"] == "error":
        return {"SKIP_AGENT": "true", "REASON": leaf["error"]}

    if leaf["kind"] == "script":
        return {
            "ROLE_NAME": "",
            "RUN_SCRIPT": "true",
            "SCRIPT_PATH": leaf["script_path"],
            "SCRIPT_ARGS": leaf["script_args"],
            "SCRIPT_TIMEOUT_MINUTES": str(leaf["script_timeout"] or ""),
            "WORKFLOW_NAME": leaf["workflow"],
            "WORKFLOW_STEP": leaf["step"],
            "PARENT_WORKFLOW": workflow.name,
            "PARENT_WORKFLOW_STEP": current_step.name,
            "STEP_SKILLS": leaf["skill"],
            "STEP_PROMPT_FILE": _write_step_prompt_file(leaf["prompt"]),
            "SKIP_AGENT": "true",
            "REASON": f"step '{leaf['step']}' in sub-workflow '{leaf['workflow']}' is a script step",
        }

    prompt_file = TASK_TO_PROMPT.get(leaf["agent"])
    if not prompt_file:
        return {
            "SKIP_AGENT": "true",
            "REASON": (
                f"Unknown agent '{leaf['agent']}' in sub-workflow "
                f"'{leaf['workflow']}' (step '{leaf['step']}')"
            ),
        }

    return {
        "ROLE_NAME": leaf["agent"],
        "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
        "WORKFLOW_NAME": leaf["workflow"],
        "WORKFLOW_STEP": leaf["step"],
        "WORKFLOW_NEXT": leaf["workflow_next"],
        "PARENT_WORKFLOW": workflow.name,
        "PARENT_WORKFLOW_STEP": current_step.name,
        "STEP_SKILLS": leaf["skill"],
        "STEP_PROMPT_FILE": _write_step_prompt_file(leaf["prompt"]),
        "SKIP_AGENT": "false",
    }


def resolve_sub_workflow_entry(
    current_step: WorkflowStep,
    workflow: WorkflowDefinition,
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
) -> Optional[dict]:
    """Public entry point for sub-workflow entry resolution.

    Delegates to :func:`_resolve_sub_workflow_entry` (same contract) so that
    other resolution paths (e.g. ``/workflow force-next-step``, issue #599)
    can force-enter a sub-workflow step with exactly the same child-loading
    and parent-reference logic as the normal happy path. ``scaffold_branch``
    enables fetch-on-miss for branch-mounted children (issue #604).
    """
    return _resolve_sub_workflow_entry(
        current_step, workflow, workflows_dir, prompt_base, scaffold_branch
    )


def _find_workflow_step(workflow: WorkflowDefinition, step_name: str) -> Optional[WorkflowStep]:
    """Find a step by name within a loaded workflow."""
    return next((s for s in workflow.steps if s.name == step_name), None)


def _condition_next_for(step: WorkflowStep, branch_key: str) -> Optional[str]:
    """Resolve a step's next target honoring conditions against a branch key."""
    if not branch_key or not step.conditions:
        return step.next
    for condition in step.conditions:
        if condition.on_result == branch_key:
            return condition.next
    for condition in step.conditions:
        if condition.on_result == "default":
            return condition.next
    return step.next


def _next_step_resolution(
    next_step: WorkflowStep,
    next_step_name: str,
    cur_wf: WorkflowDefinition,
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str],
    parent_workflow: str,
    parent_step: str,
) -> dict:
    """Resolve the parent's next step, re-entering a nested reference.

    When the next step itself mounts a sub-workflow (issue #609, e.g.
    ``tmp-impl``'s ``step_2: workflow: dev-review-loop``), the resolution
    descends to the innermost runnable leaf and records the immediate
    nesting level as the new parent pair. Skills/prompt from the reference
    step are propagated to the leaf.
    """
    if next_step.workflow or next_step.path or next_step.repo:
        entry = _resolve_sub_workflow_entry(
            next_step, cur_wf, workflows_dir, prompt_base, scaffold_branch
        )
        if entry is None:
            return {
                "SKIP_AGENT": "true",
                "REASON": f"Step '{next_step_name}' is not a valid sub-workflow step",
            }
        if entry.get("SKIP_AGENT") == "true":
            return entry
        return {
            **entry,
            "WORKFLOW_NAME": cur_wf.name,
            "WORKFLOW_STEP": next_step_name,
            "WORKFLOW_NEXT": next_step.next or "",
            "PARENT_WORKFLOW": cur_wf.name,
            "PARENT_WORKFLOW_STEP": next_step.name,
        }

    if next_step.script is not None:
        return {
            "ROLE_NAME": "",
            "PROMPT_LOCATION": "",
            "WORKFLOW_NAME": cur_wf.name,
            "WORKFLOW_STEP": next_step_name,
            "WORKFLOW_NEXT": next_step.next or "",
            "PARENT_WORKFLOW": parent_workflow,
            "PARENT_WORKFLOW_STEP": parent_step,
            "STEP_SKILLS": _skills_to_string(next_step.skill),
            "STEP_PROMPT_FILE": "",
            "SCRIPT_PATH": next_step.script.path,
            "SCRIPT_ARGS": " ".join(next_step.script.args or []),
            "SCRIPT_TIMEOUT_MINUTES": str(next_step.script.timeout_minutes or ""),
            "SKIP_AGENT": "false",
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
        "WORKFLOW_NAME": cur_wf.name,
        "WORKFLOW_STEP": next_step_name,
        "WORKFLOW_NEXT": next_step.next or "",
        "PARENT_WORKFLOW": parent_workflow,
        "PARENT_WORKFLOW_STEP": parent_step,
        "STEP_SKILLS": _skills_to_string(next_step.skill),
        "STEP_PROMPT_FILE": _write_step_prompt_file(next_step.prompt),
        "SKIP_AGENT": "false",
    }


def _resolve_return_to_parent(
    parent_workflow_name: str,
    parent_workflow_step_name: str,
    branch_key: str,
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
    outer_parent_workflow: str = "",
    outer_parent_step: str = "",
) -> Optional[dict]:
    """Resolve returning to parent workflow after sub-workflow completion.

    Loads the parent workflow, finds the step that was the sub-workflow entry
    point, and resolves the next step after it using conditional routing.
    When that next step is itself a sub-workflow reference (issue #609, e.g.
    ``tmp-impl``'s ``step_2: workflow: dev-review-loop``), the resolution
    re-enters it via :func:`_next_step_resolution` so the scaffold's
    per-step plan advances one loop at a time.
    """
    # Load parent workflow — supports names and repo-root-relative paths
    # (issue #468): path-like references resolve outside workflows/.
    # Plain names honor consumer-local precedence (issue #529).
    resolved = ""
    if is_path_like_reference(parent_workflow_name):
        resolved = resolve_workflow_reference(parent_workflow_name, workflows_dir)
    else:
        try:
            resolved = str(load_workflow_with_dirs(
                parent_workflow_name, workflows_dir, local_workflows_dir or "",
            ))
        except (FileNotFoundError, ValueError):
            resolved = ""
    if not resolved:
        resolved = str(Path(workflows_dir) / f"{parent_workflow_name}.yaml")
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

    sub_wf_step = _find_workflow_step(parent_wf, parent_workflow_step_name)
    if sub_wf_step is None:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Step '{parent_workflow_step_name}' not found in parent workflow",
        }

    next_step_name = _condition_next_for(sub_wf_step, branch_key)

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

    next_step = _find_workflow_step(parent_wf, next_step_name)
    if next_step is None:
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Next step '{next_step_name}' not found in parent workflow",
        }

    return _next_step_resolution(
        next_step, next_step_name, parent_wf, workflows_dir, prompt_base,
        scaffold_branch, outer_parent_workflow, outer_parent_step,
    )


def resolve_workflow_step(
    task: str,
    workflow_name: str,
    step_name: str,
    branch_key: str,
    workflows_dir: str,
    prompt_base: str,
    parent_workflow: str = "",
    parent_workflow_step: str = "",
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
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
        local_workflows_dir: Optional consumer-local workflows dir with
            precedence over ``workflows_dir`` for plain-name lookups (issue #529).
        scaffold_branch: Branch carrying a branch-mounted child workflow;
            enables fetch-on-miss for children absent from the local checkout
            (issue #604).

    Returns:
        Dict with routing variables.
    """
    # Load workflow definition — supports names and repo-root-relative paths
    # (issue #468): path-like references resolve outside workflows/.
    # Plain names scan local dir first (issue #529).
    if is_path_like_reference(workflow_name):
        resolved = resolve_workflow_reference(workflow_name, workflows_dir)
    else:
        try:
            resolved = load_workflow_with_dirs(workflow_name, workflows_dir, local_workflows_dir or "")
        except FileNotFoundError:
            resolved = Path(workflows_dir) / f"{workflow_name}.yaml"
    if not Path(resolved).exists():
        # Fetch-on-miss (issue #604): a branch-mounted child workflow (e.g.
        # the generated ``tmp_impl/workflow.yaml``) is absent from the
        # default-branch checkout; fetch it from ``scaffold_branch``.
        fetched = _fetch_scaffold_workflow_file(
            workflow_name, workflows_dir, scaffold_branch,
            parent_workflow, parent_workflow_step,
        )
        if fetched:
            resolved = fetched
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
            "STEP_SKILLS": "",
            "STEP_PROMPT_FILE": "",
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
            "STEP_SKILLS": "",
            "STEP_PROMPT_FILE": "",
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
            parent_workflow, parent_workflow_step, branch_key, workflows_dir,
            prompt_base, scaffold_branch, local_workflows_dir,
        )

    # Check if this is a sub-workflow entry step (local or remote)
    if current_step.workflow or current_step.repo:
        return _resolve_sub_workflow_entry(
            current_step, current_wf, workflows_dir, prompt_base, scaffold_branch
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
        "STEP_SKILLS": _skills_to_string(current_step.skill),
        "STEP_PROMPT_FILE": _write_step_prompt_file(current_step.prompt),
        "SKIP_AGENT": "false",
    }


def _load_workflow_by_reference(
    workflow_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> Optional[WorkflowDefinition]:
    """Load a workflow by name or repo-root-relative path reference.

    Plain names are scanned with the consumer-local directory taking
    precedence (issue #529); path-like references resolve as before.
    """
    if not is_valid_workflow_reference(workflow_name):
        return None
    try:
        if is_path_like_reference(workflow_name):
            reference = resolve_workflow_reference(workflow_name, workflows_dir)
            return load_workflow(str(reference))
        candidate = load_workflow_with_dirs(workflow_name, workflows_dir, local_workflows_dir or "")
        return load_workflow(str(candidate))
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


def _sub_workflow_entry_marker(
    child_reference: str,
    parent_workflow: WorkflowDefinition,
    parent_step: WorkflowStep,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
) -> Optional[dict]:
    """Build the 'enter sub-workflow' result structure.

    Descends to the child's innermost runnable leaf (issue #609) — a child
    whose first step is itself a sub-workflow reference (e.g. the scaffold's
    ``tmp-impl`` steps) resolves to the leaf workflow's leaf step instead of
    carrying ``task=None``. The recorded ``workflow`` stays the reference
    being entered (the immediate parent for the return leg) while
    ``step`` is the leaf step to run. Returns ``None`` when the child
    cannot be loaded or has no runnable leaf; the caller then falls through
    to the no-runnable-source skip.
    """
    leaf = _resolve_entry_leaf_for_step(
        parent_step, workflows_dir, scaffold_branch, local_workflows_dir
    )
    if "error" in leaf:
        print(f"Warning: {leaf['error']}", file=sys.stderr)
        return None
    return {
        "entered_sub_workflow": True,
        "task": leaf["task"],
        "step": leaf["step"],
        "workflow": child_reference,
        "parent_workflow": parent_workflow.name,
        "parent_workflow_step": parent_step.name,
    }


def _return_to_parent_marker(
    parent_workflow: WorkflowDefinition,
    parent_step_name: str,
    branch_key: Optional[str],
    parent_reference: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
) -> Optional[dict]:
    """Build the 'exit sub-workflow' result structure.

    Given the parent's sub-workflow reference step, resolves the parent's
    next step after it, honoring that step's conditions against the incoming
    branch key. When that next step is itself a sub-workflow reference
    (issue #609, e.g. ``tmp-impl``'s ``step_2: workflow: dev-review-loop``),
    the marker re-enters it via the entry-leaf descent so the recorded
    ``workflow``/``step`` pair is the leaf to run and the parent tracking
    fields point at the parent level. ``parent_reference`` is the state's
    workflow name/path used to restore ``config_workflow``. Returns None
    when the parent step is missing or itself terminal (overall terminal).
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

    # The parent's next step may itself mount a sub-workflow: re-enter it
    # through the leaf descent instead of reading ``agent`` (which is None
    # on a reference step — issue #609).
    if target_step.workflow or target_step.path or target_step.repo:
        marker = _sub_workflow_entry_marker(
            target_step.workflow or target_step.path or target_step.repo,
            parent_workflow, target_step, workflows_dir, local_workflows_dir,
            scaffold_branch,
        )
        if marker is not None:
            effective_branch = _branch_key_for(parent_step, branch_key or "")
            if effective_branch:
                marker["branch"] = effective_branch
            return marker
        return None

    marker: dict = {
        "exited_sub_workflow": True,
        "task": target_step.agent,
        "step": target_step.name,
        "workflow": parent_reference,
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
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
) -> Optional[dict]:
    """Exit to the parent when a sub-workflow cannot advance internally.

    Used when the current step is terminal (``next: null``) or its target
    step is missing. Returns the parent's next step after the sub-workflow
    reference, or falls back to ``agent_hint`` / ``None`` when no parent is
    set (overall terminal).
    """
    if parent_workflow and parent_workflow_step:
        parent_wf = _load_workflow_by_reference(parent_workflow, workflows_dir, local_workflows_dir)
        if parent_wf is not None:
            marker = _return_to_parent_marker(
                parent_wf, parent_workflow_step, branch_result, parent_workflow,
                workflows_dir, local_workflows_dir, scaffold_branch,
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
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
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
        scaffold_branch: Branch carrying a branch-mounted child workflow;
            enables fetch-on-miss for children absent from the local checkout
            (issue #604).

    Returns:
        ``{"task", "step"}`` for linear routing, ``{"task", "step", "branch"}``
        for conditional branches, ``{"task": "<hint>"}`` for agent hint
        fallback, entry/exit markers for sub-workflow transitions, or
        ``None`` for terminal/missing cases.
    """
    workflow = _load_workflow_by_reference(workflow_name, workflows_dir, local_workflows_dir)
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
    # scope for the trigger-side resolver). The marker descends to the
    # innermost runnable leaf, so a child whose first step is itself a
    # sub-workflow reference resolves to that leaf (issue #609).
    child_ref = _resolve_sub_workflow_ref_target(current_step)
    if child_ref:
        marker = _sub_workflow_entry_marker(
            child_ref, workflow, current_step, workflows_dir,
            local_workflows_dir, scaffold_branch,
        )
        if marker is not None:
            return marker

    # Terminal step: return to parent when inside a sub-workflow
    if _is_terminal_step(current_step):
        if parent_workflow and parent_workflow_step:
            parent_wf = _load_workflow_by_reference(parent_workflow, workflows_dir, local_workflows_dir)
            if parent_wf is not None:
                marker = _return_to_parent_marker(
                    parent_wf, parent_workflow_step, branch_result, parent_workflow,
                    workflows_dir, local_workflows_dir, scaffold_branch,
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
            parent_workflow, parent_workflow_step, branch_result, workflows_dir, agent_hint,
            local_workflows_dir, scaffold_branch,
        )

    # Look up target step to get its agent
    target_step = None
    for step in workflow.steps:
        if step.name == target_step_name:
            target_step = step
            break

    if target_step is None:
        # Broken next reference: the workflow is not terminal, so no parent
        # return and no hint fallback — resolution fails cleanly.
        return None

    # Deterministic script step (issue #541): distinct marker dict, same
    # pattern as the sub-workflow lifecycle markers — no "task" key because
    # downstream consumers assume a non-None agent name.
    if target_step.script is not None:
        result = {
            "run_script": True,
            "script_path": target_step.script.path,
            "script_args_str": " ".join(target_step.script.args or []),
            "timeout_minutes": target_step.script.timeout_minutes,
            "step": target_step.name,
        }
    else:
        result = {
            "task": target_step.agent,
            "step": target_step.name,
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
