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
import re
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


# Plan step names follow the scaffold convention ``step_N`` (1-based, issue
# #462/#613): a branch-mounted plan workflow (``tmp_impl/workflow.yaml``) has
# one ``step_N`` per plan item, each mounting the reusable per-step loop.
_PLAN_STEP_NAME_RE = re.compile(r"^step_(\d+)$")


def _plan_step_index(step_name: str) -> Optional[int]:
    """Return the 1-based plan step index for a ``step_N`` name, else None."""
    match = _PLAN_STEP_NAME_RE.match(step_name)
    return int(match.group(1)) if match else None


def _parent_plan_position(
    plan_wf: Optional[WorkflowDefinition], step_name: str
) -> tuple[str, str]:
    """Derive the plan position (index, total) of a plan step.

    The plan workflow (``tmp_impl/workflow.yaml``) is generated with one
    ``step_N`` per plan item, so the 1-based index comes from the ``step_N``
    name and the total from the step count (issue #613). Returns
    ``("", "")`` when the position is not derivable (non-plan child), so the
    caller emits empty env values — additive, never breaks existing consumers.
    """
    if plan_wf is None or not plan_wf.steps:
        return "", ""
    index = _plan_step_index(step_name)
    if index is None:
        return "", ""
    return str(index), str(len(plan_wf.steps))


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


_WORKFLOW_FILE_SUFFIXES = (".yaml", ".yml")

# Bound on sub-workflow nesting depth (review round 2, issue #609): the
# cycle guard covers infinite descent, but a deep non-cyclic chain must also
# terminate instead of recursing unbounded.
_MAX_SUB_WORKFLOW_DEPTH = 10


def _workflow_ref_stem(ref: str) -> str:
    """Return the canonical file stem of a workflow reference.

    Used for path-reference matching (issue #609): two references denote
    the same workflow file when their stems agree.
    """
    base = ref.rsplit("/", 1)[-1]
    for suffix in _WORKFLOW_FILE_SUFFIXES:
        if base.lower().endswith(suffix):
            return base[: -len(suffix)]
    return base


def _ref_matches_workflow(
    ref: Optional[str], workflow_name: str, target_stem: str
) -> bool:
    """Return True when a step's ``workflow:`` reference targets
    ``workflow_name``.

    Plain-name references match on exact name; path-like references match
    on the file stem so a child mounted as ``tmp_impl/workflow.yaml`` is
    found when its loaded ``name`` is used in the state (issue #609).
    """
    if not ref:
        return False
    if ref == workflow_name:
        return True
    return is_path_like_reference(ref) and _workflow_ref_stem(ref) == target_stem


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
        local_workflows_dir=local_workflows_dir,
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
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Resolve the innermost runnable leaf of a (possibly nested) child.

    Descends through child first-steps that are themselves ``workflow:``/
    ``path:`` sub-workflow references until a runnable leaf is found (an
    ``agent`` step, or a deterministic ``script`` step). A child whose first
    step has no runnable source is a leaf in its own right and is surfaced as
    an error (issue #609).

    Two guards bound the descent (review round 2): a *cycle* guard keyed on
    the loaded workflow's canonical ``name`` (plus a pre-load check on the
    reference stem, so a path-based self/cycle is caught before its fetch),
    and a *max-depth* bound (``_MAX_SUB_WORKFLOW_DEPTH``) so a deep
    non-cyclic chain terminates instead of unbounded descent.

    Returns a dict with:
    - ``kind``: "agent" | "script" | "error"
    - ``error``: reason string (only when kind == "error")
    - ``agent``/``step``/``workflow``/``workflow_next``: leaf step details
      (agent/script)
    - ``skill``/``prompt``: merged skills + step prompt (parent entry-step
      skills take precedence over the leaf step's own skills)
    """
    entry_ref = entry_step.workflow or entry_step.path
    wf = child_wf
    leaf = wf.steps[0] if wf.steps else None
    depth = 0
    while leaf is not None and not leaf.agent and leaf.script is None:
        # A first-step with no runnable source is itself a leaf: surface it.
        if not leaf.workflow and not leaf.path and not leaf.repo:
            break
        ref = leaf.workflow or leaf.path
        if not ref:
            break
        # Pre-load cycle check on the reference stem: catches a path-based
        # self/cycle (stem matches a visited name) before paying for the
        # fetch. The authoritative check is on the loaded child's name below.
        if _workflow_ref_stem(ref) in visited:
            return {
                "kind": "error",
                "error": (
                    f"sub-workflow reference cycle detected while entering "
                    f"'{entry_ref}' (at '{ref}'; "
                    f"visited: {', '.join(sorted(visited))})"
                ),
            }
        if depth >= _MAX_SUB_WORKFLOW_DEPTH:
            return {
                "kind": "error",
                "error": (
                    f"max sub-workflow nesting depth "
                    f"({_MAX_SUB_WORKFLOW_DEPTH}) exceeded while entering "
                    f"'{entry_ref}' (at '{ref}'); "
                    f"visited: {', '.join(sorted(visited))}"
                ),
            }
        child = _load_child_workflow(
            ref, workflows_dir, scaffold_branch, fetch_cache,
            local_workflows_dir,
        )
        if child is None:
            return {
                "kind": "error",
                "error": (
                    f"Sub-workflow not found: {ref} "
                    f"(nested under '{entry_ref}')"
                ),
            }
        # Canonical cycle check on the loaded name: a reference may be a
        # path whose stem differs from the workflow's declared name, so the
        # name (already in ``visited`` for the top child) is the true key.
        if child.name in visited:
            return {
                "kind": "error",
                "error": (
                    f"sub-workflow reference cycle detected while entering "
                    f"'{entry_ref}' (at '{ref}'; "
                    f"visited: {', '.join(sorted(visited))})"
                ),
            }
        visited.add(child.name)
        depth += 1
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
    outer_parent_workflow: str = "",
    outer_parent_step: str = "",
    local_workflows_dir: Optional[str] = None,
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
        local_workflows_dir=local_workflows_dir,
    )
    if leaf["kind"] == "error":
        return {"SKIP_AGENT": "true", "REASON": leaf["error"]}

    # The flat parent-pair state holds one level, so on a nested re-entry the
    # pair that must survive is the outermost one (issue #609): when an outer
    # pair is on record and differs from this immediate parent, keep it so the
    # chain can unwind one level per return. The immediate parent is
    # re-derivable from the re-entry itself.
    parent_wf_name = (
        outer_parent_workflow
        if outer_parent_workflow and outer_parent_workflow != workflow.name
        else workflow.name
    )
    parent_step_name = (
        outer_parent_step
        if outer_parent_workflow and outer_parent_workflow != workflow.name
        else current_step.name
    )

    # Plan position pointer (issue #613): record which plan step this entry
    # implements. The branch-mounted plan (``tmp_impl/workflow.yaml``) is
    # generated with one ``step_N`` per plan item, so the current plan step is
    # either ``current_step`` itself — a re-entry where ``_next_step_resolution``
    # advanced the plan one loop at a time, so the containing ``workflow`` is the
    # plan — or the entered child's first step — a first entry where ``child_wf``
    # IS the plan and ``step_1`` is being entered. ``("", "")`` for non-plan
    # children; additive keys, existing consumers unaffected.
    if _plan_step_index(current_step.name) is not None:
        plan_step_index, plan_total_steps = _parent_plan_position(
            workflow, current_step.name
        )
    elif child_wf.steps and _plan_step_index(child_wf.steps[0].name) is not None:
        plan_step_index, plan_total_steps = _parent_plan_position(
            child_wf, child_wf.steps[0].name
        )
    else:
        plan_step_index, plan_total_steps = "", ""

    if leaf["kind"] == "script":
        return {
            "ROLE_NAME": "",
            "RUN_SCRIPT": "true",
            "SCRIPT_PATH": leaf["script_path"],
            "SCRIPT_ARGS": leaf["script_args"],
            "SCRIPT_TIMEOUT_MINUTES": str(leaf["script_timeout"] or ""),
            "WORKFLOW_NAME": leaf["workflow"],
            "WORKFLOW_STEP": leaf["step"],
            "PARENT_WORKFLOW": parent_wf_name,
            "PARENT_WORKFLOW_STEP": parent_step_name,
            "PARENT_PLAN_STEP_INDEX": plan_step_index,
            "PARENT_PLAN_TOTAL_STEPS": plan_total_steps,
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
        "PARENT_WORKFLOW": parent_wf_name,
        "PARENT_WORKFLOW_STEP": parent_step_name,
        "PARENT_PLAN_STEP_INDEX": plan_step_index,
        "PARENT_PLAN_TOTAL_STEPS": plan_total_steps,
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
    local_workflows_dir: Optional[str] = None,
) -> Optional[dict]:
    """Public entry point for sub-workflow entry resolution.

    Delegates to :func:`_resolve_sub_workflow_entry` (same contract) so that
    other resolution paths (e.g. ``/workflow force-next-step``, issue #599)
    can force-enter a sub-workflow step with exactly the same child-loading
    and parent-reference logic as the normal happy path. ``scaffold_branch``
    enables fetch-on-miss for branch-mounted children (issue #604).
    """
    return _resolve_sub_workflow_entry(
        current_step, workflow, workflows_dir, prompt_base, scaffold_branch,
        local_workflows_dir=local_workflows_dir,
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
    local_workflows_dir: Optional[str] = None,
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
            next_step, cur_wf, workflows_dir, prompt_base, scaffold_branch,
            outer_parent_workflow=parent_workflow,
            outer_parent_step=parent_step,
            local_workflows_dir=local_workflows_dir,
        )
        if entry is None:
            return {
                "SKIP_AGENT": "true",
                "REASON": f"Step '{next_step_name}' is not a valid sub-workflow step",
            }
        if entry.get("SKIP_AGENT") == "true":
            return entry
        # Descend to the leaf (issue #609): keep the leaf workflow/step the
        # entry resolved (``WORKFLOW_NAME``/``WORKFLOW_STEP``) rather than
        # overriding back to the immediate reference. The outer parent pair
        # (threaded in via ``outer_parent_*`` and the entry's prefer-outer
        # logic) survives the flat state so the next return-to-parent resumes
        # the outer level. The immediate parent is re-derivable from the graph.
        return entry

    if next_step.script is not None:
        # Deterministic script successor (issue #541): same contract as the
        # nested-entry script branch and the ``run_script`` path in
        # ``build_result`` — SKIP_AGENT stays true so the agent section is
        # skipped, and RUN_SCRIPT lets the GHA script gate (and the posting
        # pipeline) pick the run up and advance state. No REASON is emitted,
        # so the "Workflow stopped" skip comment never fires (issue #609).
        return {
            "ROLE_NAME": "",
            "RUN_SCRIPT": "true",
            "SCRIPT_PATH": next_step.script.path,
            "SCRIPT_ARGS": " ".join(next_step.script.args or []),
            "SCRIPT_TIMEOUT_MINUTES": str(next_step.script.timeout_minutes or ""),
            "WORKFLOW_NAME": cur_wf.name,
            "WORKFLOW_STEP": next_step_name,
            "WORKFLOW_NEXT": next_step.next or "",
            "PARENT_WORKFLOW": parent_workflow,
            "PARENT_WORKFLOW_STEP": parent_step,
            "STEP_SKILLS": _skills_to_string(next_step.skill),
            "STEP_PROMPT_FILE": "",
            "SKIP_AGENT": "true",
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


def _find_outer_parent(
    workflow_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """Re-derive the immediate parent of ``workflow_name`` from the graph.

    The runtime's flat state carries a single nesting level, so a three-level
    nested sub-workflow (issue #609, e.g. ``parent -> mid -> leaf``) must
    recover the *outer* level that is not on the state. Scanning the workflow
    files for a step that references ``workflow_name`` (via a ``workflow:``
    name reference) yields the outer ``(workflow, step)`` pair; that reference
    edge is authoritative and lets the chain unwind one level per return.

    Returns ``None`` when no workflow locally references ``workflow_name``
    (it is not nested under a known parent). Plain names are matched exactly;
    path-like references are matched by file stem so a child mounted as
    ``tmp_impl/workflow.yaml`` is found when its loaded ``name`` is recorded
    in the state. Repo references are out of scope for reverse lookup.

    Raises ``ValueError`` when multiple distinct workflows reference the
    child: the outer pair is then ambiguous and resuming the first match
    would silently pick the wrong workflow (review round 2, issue #609).
    Multiple steps inside a *single* referrer workflow (e.g. a plan whose
    steps each mount the same reusable loop) are not ambiguous about which
    workflow to resume; the first such step is used.
    """
    if not workflow_name:
        return None
    target_stem = _workflow_ref_stem(workflow_name)
    candidates: list[Path] = [Path(workflows_dir)]
    if local_workflows_dir:
        candidates.append(Path(local_workflows_dir))
    referrers: list[tuple[str, str]] = []
    seen_files: set[str] = set()
    for base in candidates:
        if not base.exists():
            continue
        # Recursive scan: branch-mounted plans (issue #604) live in
        # subdirectories (e.g. ``tmp_impl/workflow.yaml``), so the outer
        # referrer is not always a top-level file.
        for wf_file in sorted(base.rglob("*.yaml")) + sorted(base.rglob("*.yml")):
            key = str(wf_file.resolve())
            if key in seen_files:
                continue
            seen_files.add(key)
            try:
                wf = load_workflow(str(wf_file))
            except (FileNotFoundError, ValueError):
                continue
            for step in wf.steps:
                if _ref_matches_workflow(step.workflow, workflow_name, target_stem):
                    referrers.append((wf.name, step.name))
    referrer_workflows = {w for w, _ in referrers}
    if len(referrer_workflows) > 1:
        # Ambiguous: a reusable child mounted by multiple workflows cannot
        # be reverse-resolved unambiguously (review round 2, issue #609).
        # Surface a precise error instead of silently resuming the wrong one.
        detail = ", ".join(f"{w}.{s}" for w, s in referrers)
        raise ValueError(
            f"ambiguous outer parent for '{workflow_name}': multiple workflows "
            f"reference it ({detail})"
        )
    return referrers[0] if referrers else None


def _resolve_return_to_parent(
    parent_workflow_name: str,
    parent_workflow_step_name: str,
    branch_key: str,
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
    _visited: tuple[str, ...] = (),
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

    # Re-derive the outer parent from the graph (issue #609, three-level
    # nesting). The runtime's flat state carries only the immediate parent
    # (e.g. ``mid``), so the *outer* pair (e.g. ``(parent, mount)``) is not on
    # record and must be recovered from the workflow files. The reference edge
    # is authoritative and lets the chain unwind one level per return. When
    # the immediate parent is not nested under a known outer (top-level), the
    # recorded parent is empty — the on-record ``outer_parent_*`` args carry
    # the *immediate* parent (see the call site), not an outer level, so they
    # are intentionally not used here.
    try:
        graph_outer = _find_outer_parent(
            parent_wf.name, workflows_dir, local_workflows_dir
        )
    except ValueError as e:
        # Multiple workflows mount the same child: the outer resume target is
        # ambiguous. Fail loudly instead of resuming the wrong workflow.
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Ambiguous sub-workflow exit: {e}",
        }
    eff_outer_wf = graph_outer[0] if graph_outer else ""
    eff_outer_step = graph_outer[1] if graph_outer else ""

    if not next_step_name:
        # Sub-workflow step was terminal in parent. With an outer pair the
        # chain must unwind one more level instead of ending: continue the
        # return from the outer parent so the outermost workflow resumes after
        # the mount step. ``_visited`` bounds the recursion against reference
        # cycles (issue #609).
        if eff_outer_wf and eff_outer_wf != parent_wf.name \
                and parent_wf.name not in _visited:
            return _resolve_return_to_parent(
                eff_outer_wf, eff_outer_step, branch_key,
                workflows_dir, prompt_base, scaffold_branch, local_workflows_dir,
                _visited=_visited + (parent_wf.name,),
            )
        # Workflow ends here
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
        scaffold_branch, eff_outer_wf, eff_outer_step, local_workflows_dir,
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

    # Check if this is a sub-workflow entry step (local or remote). The
    # incoming parent pair (issue #609) is threaded through so a nested
    # re-entry (outer ``parent`` -> ``mid`` -> ``leaf``) descends to the leaf
    # while the outer pair survives the flat state.
    if current_step.workflow or current_step.repo:
        return _resolve_sub_workflow_entry(
            current_step, current_wf, workflows_dir, prompt_base, scaffold_branch,
            outer_parent_workflow=parent_workflow,
            outer_parent_step=parent_workflow_step,
            local_workflows_dir=local_workflows_dir,
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
    outer_parent_workflow: str = "",
    outer_parent_step: str = "",
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

    Invariant (trigger vs. runtime asymmetry): this marker records
    ``workflow`` = the reference being entered, while the runtime entry path
    (``_resolve_sub_workflow_entry``) records ``WORKFLOW_NAME`` = the innermost
    leaf workflow. Both route correctly — the dispatch path re-resolves from
    the reference step and the runtime entry is the state authority — but the
    two must stay consistent in descent (same leaf, same skill merge); a
    change to either side must preserve this invariant (pinned by
    ``test_trigger_side_marker_matches_leaf``).
    """
    leaf = _resolve_entry_leaf_for_step(
        parent_step, workflows_dir, scaffold_branch, local_workflows_dir
    )
    if "error" in leaf:
        print(f"Warning: {leaf['error']}", file=sys.stderr)
        return None
    # Prefer the outer parent pair when it differs from the immediate parent
    # (issue #609): the flat parent-pair state can hold only one level, so the
    # pair that survives a nested re-entry must be the outermost one. The
    # immediate parent is re-derivable from the re-entry itself.
    parent_wf_name = (
        outer_parent_workflow
        if outer_parent_workflow and outer_parent_workflow != parent_workflow.name
        else parent_workflow.name
    )
    parent_step_name = (
        outer_parent_step
        if outer_parent_workflow and outer_parent_workflow != parent_workflow.name
        else parent_step.name
    )
    return {
        "entered_sub_workflow": True,
        "task": leaf["task"],
        "step": leaf["step"],
        "workflow": child_reference,
        "parent_workflow": parent_wf_name,
        "parent_workflow_step": parent_step_name,
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
