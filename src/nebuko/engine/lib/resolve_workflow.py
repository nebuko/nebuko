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
    {"task": "architect", "workflow": "default", "step": "analyze"}
    EOF

Output (written to --env-file):
    ROLE_NAME=architect
    PROMPT_LOCATION=.agents/prompts/architect.md
    WORKFLOW_NAME=default
    WORKFLOW_STEP=analyze
    WORKFLOW_NEXT=context
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Union

import yaml

from config import (
    SkillSpec,
    WorkflowStep,
    WorkflowDefinition,
    load_config,
    load_workflow,
    load_workflow_with_dirs,
    is_path_like_reference,
    is_valid_workflow_reference,
    resolve_workflow_reference,
    StepRef,
    StepPath,
    WorkflowPosition,
)

try:
    from remote_workflow import fetch_remote_workflow
except ImportError:
    fetch_remote_workflow = None  # type: ignore

#: Routing fallback for an unreadable agent config (issue #476). The live roster
#: is the source of truth; this map only keeps routing alive when the config is
#: missing or malformed.
_FALLBACK_TASK_TO_PROMPT = {
    "architect": "architect.md",
    "context": "context.md",
    "dev": "dev.md",
    "review": "review.md",
    "po": "po.md",
}


def _load_task_to_prompt() -> dict[str, str]:
    """Agent name → prompt-file basename, derived from the live roster.

    Only the basename is kept: ``prompt_base`` already points at the prompt
    directory, so adding or renaming an agent in ``agent-config.yaml`` is enough
    to route it here.
    """
    try:
        config = load_config(os.environ.get("AGENT_CONFIG_PATH", "agent-config.yaml"))
        derived = {
            name: os.path.basename(agent.prompt_file)
            for name, agent in config.agents.items()
            if agent.prompt_file
        }
    except Exception as e:  # noqa: BLE001 - routing must not crash on a bad config
        print(
            f"Warning: agent roster unreadable ({e}); using fallback routing map",
            file=sys.stderr,
        )
        return dict(_FALLBACK_TASK_TO_PROMPT)

    if not derived:
        print(
            "Warning: agent roster is empty; using fallback routing map",
            file=sys.stderr,
        )
        return dict(_FALLBACK_TASK_TO_PROMPT)
    return derived


#: Agent name → prompt-file basename, derived from the roster (issue #476).
TASK_TO_PROMPT = _load_task_to_prompt()


def resolve_complexity_tier(
    dispatch_complexity: Optional[str],
    step_complexity: Optional[str],
) -> str:
    """Resolve the effective complexity tier (issue #787).

    Precedence: an explicit dispatch ``complexity`` (the agent's runtime
    choice) overrides the tier declared on the workflow step, which overrides
    the ``"medium"`` default. Empty/absent values count as unset.
    """
    return dispatch_complexity or step_complexity or "medium"


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
        "STEP_COMPLEXITY": "",
        "STEP_PROMPT_REPLACE": "",
        "SKIP_AGENT": "false",
    }


def _skill_names(skill_list: list | None) -> list[str]:
    """Flatten skill entries (bare names or :class:`SkillSpec`) to names."""
    return list(SkillSpec.iter_names(skill_list))


def _specs_to_env(specs: list) -> str:
    """Serialize skill entries for the flat ``KEY=value`` hand-off.

    Imported lazily: ``resolve_config`` imports this module, so a module-level
    import of its ``skill_context`` feature would close a cycle.
    """
    from resolve_config.features.skill_context.skill_context import specs_to_env

    return specs_to_env(specs)


def _skills_to_string(skill_list: list | None) -> str:
    """Convert an optional skill list to a comma-separated string."""
    return ",".join(_skill_names(skill_list))


def _skill_specs_json(skill_list: list | None) -> str:
    """Serialize a step's skill entries (mode included) for the hand-off."""
    return _specs_to_env(SkillSpec.parse_all(list(skill_list or ())))


def _skill_specs_from_names(names: str) -> str:
    """Inject-mode specs for a merged name string.

    The sub-workflow descent collapses a leaf's skills to a name string, so the
    mount-descent path carries no mode information; entries default to
    ``inject``, which is also the bare-string default.
    """
    items = [name for name in names.split(",") if name]
    return _specs_to_env(SkillSpec.parse_all(items))


def _prompt_replace_flag(enabled: bool | None) -> str:
    """Serialise a step's prompt-replacement mode for the KEY=value result.

    Emits ``"true"`` only when the step declares ``prompt_replace: true``; the
    consumer treats every other value as the additive (default) behaviour
    (issue #784).
    """
    return "true" if enabled else ""


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


def match_condition(
    step: WorkflowStep,
    branch_key: str,
) -> Optional[str]:
    """Pick a condition target step name for a branch key.

    Exact-match on ``on_result`` first, then ``default``; None when the
    branch is not covered by the step's conditions (or it has none).

    Public since issue #751: the offline workflow linter imports this so its
    condition rules can never drift from the runtime's actual routing.
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


def branch_key_for(
    step: WorkflowStep,
    branch_key: str,
) -> Optional[str]:
    """Return the effective branch key for a conditional step.

    Exact match wins; an unmatched key falls back to ``"default"`` only when
    a default condition exists.
    """
    if not branch_key or not step.conditions:
        return None
    if match_condition(step, branch_key) is not None:
        return branch_key
    if any(c.on_result == "default" for c in step.conditions):
        return "default"
    return None


def is_terminal_step(step: WorkflowStep) -> bool:
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


class RouteKind(Enum):  # @stub-protected
    """What kind of routing result the resolver produced (issue #923).

    The distinction that matters for the round-2 dead-end is
    ``DECLARED_FINISH`` (the workflow explicitly ends: a matched
    ``on_result`` edge or linear ``next`` with ``next: null``) versus
    ``UNRESOLVED`` (nothing matched and nothing declared an end — a config
    gap that must ping for input, never look like an honest finish).
    """

    ROUTE = "route"  # a normal ``{task, step[, branch]}`` successor
    RUN_SCRIPT = "run_script"  # a deterministic script successor (issue #541)
    SUB_WORKFLOW_ENTER = "sub_workflow_enter"  # a mounted child's leaf
    SUB_WORKFLOW_EXIT = "sub_workflow_exit"  # the parent's post-ref step
    DECLARED_FINISH = "declared_finish"  # the workflow declares its end
    UNRESOLVED = "unresolved"  # a conditions step with no matching edge


@dataclass(frozen=True)
class RouteResolution:  # @stub-protected
    """Immutable typed result of :func:`resolve_next_from_workflow` (issue #923).

    Exactly one :class:`RouteKind` variant; the optional fields carry the
    variant's payload. :meth:`as_marker` reproduces the legacy marker dict so
    unconverted call sites keep working during the migration.
    """

    kind: RouteKind
    task: str = ""
    step: str = ""
    branch: Optional[str] = None
    script: Optional[dict] = None
    workflow: str = ""
    parent_workflow: str = ""
    parent_workflow_step: str = ""
    reason: str = ""
    # A surfaced mount-resolution failure (issue #671): the resolution is not a
    # route but an error the caller must report rather than step over, so the
    # marker view reproduces the legacy ``SKIP_AGENT`` dict.
    skip_agent: bool = False

    def as_marker(self) -> Optional[dict]:
        """Legacy marker-dict view (``None`` for the member-less kinds)."""
        if self.kind is RouteKind.UNRESOLVED and self.skip_agent:
            return {"SKIP_AGENT": "true", "REASON": self.reason}
        if self.kind is RouteKind.ROUTE:
            # Omit an empty ``step`` so the marker matches the legacy
            # agent-hint fallback shape (``{"task": <hint>}``) exactly.
            marker: dict = {"task": self.task}
            if self.step:
                marker["step"] = self.step
            if self.branch:
                marker["branch"] = self.branch
            return marker
        if self.kind is RouteKind.RUN_SCRIPT:
            # Legacy script marker shape (issue #541): the flag plus the
            # script_* keys and the tagged step name.
            marker = {
                "run_script": True,
                "script_path": (self.script or {}).get("command", ""),
                "script_args": (self.script or {}).get("args", "[]"),
                "script_working_dir": (self.script or {}).get("working_dir", ""),
                "script_env": (self.script or {}).get("env", "{}"),
                "timeout_minutes": (self.script or {}).get("timeout_minutes"),
            }
            if self.step:
                marker["step"] = self.step
            return marker
        if self.kind is RouteKind.SUB_WORKFLOW_ENTER:
            marker = {
                "entered_sub_workflow": True,
                "task": self.task,
                "step": self.step,
                "workflow": self.workflow,
                "parent_workflow": self.parent_workflow,
                "parent_workflow_step": self.parent_workflow_step,
            }
            if self.branch:
                marker["branch"] = self.branch
            return marker
        if self.kind is RouteKind.SUB_WORKFLOW_EXIT:
            marker = {
                "exited_sub_workflow": True,
                "task": self.task,
                "step": self.step,
                "workflow": self.workflow,
            }
            if self.branch:
                marker["branch"] = self.branch
            return marker
        return None


def resolution_to_marker(resolution: Optional[RouteResolution]) -> Optional[dict]:
    """Compat shim: legacy marker view of a :class:`RouteResolution`."""
    return resolution.as_marker() if resolution is not None else None


@dataclass(frozen=True)
class _StepGap:
    """A step whose routing could not be resolved (implementation detail).

    Returned internally when a step's conditions are present but no edge
    matches the branch key, or when the matched target step is missing from
    the definition — the two cases :func:`resolve_next_from_workflow` maps
    onto :attr:`RouteKind.UNRESOLVED`.
    """

    step: str
    reason: str


def _marker_to_resolution(marker: Optional[dict]) -> Optional[RouteResolution]:
    """Translate a legacy marker dict (or ``None``) into a typed resolution."""
    if marker is None:
        return None
    if marker.get("SKIP_AGENT") == "true":
        # Mount-resolution failure (issue #671) is surfaced as a typed
        # SKIP_AGENT resolution: callers must report it, never step over it.
        return RouteResolution(
            kind=RouteKind.UNRESOLVED,
            step=str(marker.get("step") or ""),
            reason=str(marker.get("REASON") or ""),
            skip_agent=True,
        )
    if marker.get("run_script"):
        return RouteResolution(
            kind=RouteKind.RUN_SCRIPT,
            step=str(marker.get("step") or ""),
            script={
                "command": marker.get("script_path", ""),
                "args": marker.get("script_args", "[]"),
                "working_dir": marker.get("script_working_dir", ""),
                "env": marker.get("script_env", "{}"),
                "timeout_minutes": marker.get("timeout_minutes"),
            },
        )
    if marker.get("entered_sub_workflow"):
        return RouteResolution(
            kind=RouteKind.SUB_WORKFLOW_ENTER,
            task=str(marker.get("task") or ""),
            step=str(marker.get("step") or ""),
            branch=marker.get("branch"),
            workflow=str(marker.get("workflow") or ""),
            parent_workflow=str(marker.get("parent_workflow") or ""),
            parent_workflow_step=str(marker.get("parent_workflow_step") or ""),
        )
    if marker.get("exited_sub_workflow"):
        return RouteResolution(
            kind=RouteKind.SUB_WORKFLOW_EXIT,
            task=str(marker.get("task") or ""),
            step=str(marker.get("step") or ""),
            branch=marker.get("branch"),
            workflow=str(marker.get("workflow") or ""),
        )
    return RouteResolution(
        kind=RouteKind.ROUTE,
        task=str(marker.get("task") or ""),
        step=str(marker.get("step") or ""),
        branch=marker.get("branch"),
    )


# Back-compat private aliases (issue #751): the offline linter imports the
# public names above while the legacy call sites keep using the underscore
# forms, so the traversal semantics live in exactly one place.
_match_condition = match_condition
_branch_key_for = branch_key_for
_is_terminal_step = is_terminal_step


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
    if is_path_like_reference(name):
        return None
    if (Path(workflows_dir) / f"{name}.yaml").exists():
        return None  # local hit wins; no fetch
    declared = None
    if parent_workflow and parent_workflow_step:
        declared = _parent_step_child_reference(
            parent_workflow, parent_workflow_step, workflows_dir
        )
        # The child may already be present in the checkout even though its
        # declared workflow name (e.g. ``tmp-impl``) is not a filename: the
        # parent step's declared reference is the authoritative location.
        local = _local_child_path_for_name(declared, name, workflows_dir)
        if local:
            return local
    if not scaffold_branch:
        return None
    refs = [declared] if declared else (
        [name, f"{name}.yaml"] if not name.endswith(".yaml") else [name]
    )
    for ref in refs:
        path = _scaffold_fetch(ref, scaffold_branch)
        if path:
            return path
    return None


def _local_child_path_for_name(
    declared: Optional[str],
    name: str,
    workflows_dir: str,
) -> Optional[str]:
    """Local path of a declared child reference when it holds ``name``.

    A branch-mounted child (``tmp_impl/workflow.yaml``) may already be in the
    checkout while its declared workflow name (``tmp-impl``) is not a filename,
    so the by-name lookup misses. Re-resolving the parent step's declared
    reference fills that gap; the loaded workflow's ``name`` must match so a
    wrong file can never substitute (issue #671).
    """
    if not declared:
        return None
    try:
        path = resolve_workflow_reference(declared, workflows_dir)
    except ValueError:
        return None
    if not Path(path).exists():
        return None
    try:
        child = load_workflow(str(path))
    except (FileNotFoundError, ValueError):
        return None
    return str(path) if child.name == name else None


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


# @stub-protected
@dataclass(frozen=True)
class WorkflowLoadContext:
    """Single source of truth for *where* to find a ``WorkflowDefinition``.

    Threads the local checkout, the consumer-local workflows dir, and the
    branch-mounted scaffold through one value object so both the *entry* and
    the *exit* paths of a nested mount apply the same fetch-on-miss policy
    (issue #675): a branch-mounted child (e.g. the generated
    ``tmp_impl/workflow.yaml``) that is absent from the default-branch
    checkout is fetched from ``scaffold_branch`` instead of being silently
    dropped, which previously collapsed an intermediate mount level and
    routed to the wrong (outer) step.

    ``fetch_cache`` memoizes ``ref -> fetched path`` for the lifetime of one
    resolution so a branch-mounted child is fetched at most once.
    """

    workflows_dir: str
    local_workflows_dir: Optional[str] = None
    scaffold_branch: Optional[str] = None
    fetch_cache: Optional[dict] = field(default=None, compare=False)
    # Declared-name -> repo-root-relative mount reference (issue #812). A
    # branch-mounted child's declared ``name`` (e.g. ``tmp-impl``) is not its
    # filename (``tmp_impl/workflow.yaml``), so a by-name lookup cannot fetch
    # it: the hint carries the reference the enclosing mount step declares.
    name_refs: Optional[dict] = field(default=None, compare=False)

    # @stub-protected
    def load(self, ref: str) -> Optional[WorkflowDefinition]:
        """Load a workflow by name or repo-root-relative path reference.

        Local checkout first (consumer-local precedence for plain names); on
        a miss, fetch from ``scaffold_branch`` when set.
        """
        if not is_valid_workflow_reference(ref):
            # The chain builder passes an already-resolved absolute mount
            # path (``target_path``); load it directly. Other unsafe
            # references (traversal) are rejected for path-safety.
            if not Path(ref).is_absolute():
                return None
            return _try_load_workflow(ref)
        resolved = ""
        if not is_path_like_reference(ref) and self.local_workflows_dir:
            try:
                resolved = str(load_workflow_with_dirs(
                    ref, self.workflows_dir, self.local_workflows_dir,
                ))
            except (FileNotFoundError, ValueError):
                resolved = ""
        if not resolved:
            resolved = str(resolve_workflow_reference(ref, self.workflows_dir))
        if Path(resolved).exists():
            return _try_load_workflow(resolved)
        fetched = _fetch_workflow_from_scaffold(
            ref, self.scaffold_branch, self.fetch_cache,
        )
        return _try_load_workflow(fetched) if fetched else None

    # @stub-protected
    def load_by_name(self, name: str) -> Optional[WorkflowDefinition]:
        """Load a workflow by its declared ``name`` via recursive file scan.

        Falls back to a scaffold fetch keyed on the declared name when no
        local file declares it. When ``name_refs`` carries the mount
        reference the enclosing parent step declares (issue #812), that
        reference — not the name — drives the fetch, so a branch-mounted
        child whose filename differs from its declared name (e.g.
        ``tmp_impl/workflow.yaml`` declaring ``tmp-impl``) is located
        instead of collapsing the nesting level.
        """
        local = _scan_workflow_by_name(name, self.workflows_dir, self.local_workflows_dir)
        if local is not None:
            return local
        declared = (self.name_refs or {}).get(name)
        if declared:
            hint = _local_child_path_for_name(declared, name, self.workflows_dir)
            if hint is not None:
                return _try_load_workflow(hint)
            fetched = _fetch_workflow_from_scaffold(
                declared, self.scaffold_branch, self.fetch_cache,
            )
            loaded = _try_load_workflow(fetched) if fetched else None
            if loaded is not None:
                return loaded
        fetched = _fetch_workflow_from_scaffold(
            name, self.scaffold_branch, self.fetch_cache,
        )
        return _try_load_workflow(fetched) if fetched else None


def _try_load_workflow(path: Optional[str]) -> Optional[WorkflowDefinition]:
    """Load a workflow file, returning ``None`` on any load failure."""
    if not path:
        return None
    try:
        return load_workflow(path)
    except (FileNotFoundError, ValueError):
        return None


def _fetch_workflow_from_scaffold(
    ref: str,
    scaffold_branch: Optional[str],
    fetch_cache: Optional[dict],
) -> Optional[str]:
    """Fetch a branch-mounted workflow file, memoized per resolution.

    Returns the absolute path of the fetched file, or ``None`` when the fetch
    cannot be attempted or fails. ``fetch_cache`` (``ref -> path``) is only
    populated on a hit, so a miss is retried by the next caller.
    """
    if not scaffold_branch:
        return None
    if fetch_cache is not None and ref in fetch_cache:
        return fetch_cache[ref]
    fetched = _scaffold_fetch(ref, scaffold_branch)
    if fetched and fetch_cache is not None:
        fetch_cache[ref] = fetched
    return fetched


def _scan_workflow_by_name(
    workflow_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> Optional[WorkflowDefinition]:
    """Load a workflow by its declared ``name`` via a recursive file scan.

    The state history records a workflow's declared name (issue #657), which
    :func:`_load_workflow_by_reference` cannot resolve when the workflow is
    mounted only through a path-like reference — e.g. a branch-mounted
    ``tmp_impl/workflow.yaml`` declaring ``name: tmp-impl``. Scans the same
    directories (and subdirectories) as :func:`_find_outer_parent`. Returns
    ``None`` when no file declares that name.
    """
    if not workflow_name:
        return None
    bases = [Path(workflows_dir)]
    if local_workflows_dir:
        bases.append(Path(local_workflows_dir))
    seen: set[str] = set()
    for base in bases:
        if not base.exists():
            continue
        for wf_file in sorted(base.rglob("*.yaml")) + sorted(base.rglob("*.yml")):
            key = str(wf_file.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                wf = load_workflow(str(wf_file))
            except (FileNotFoundError, ValueError):
                continue
            if wf.name == workflow_name:
                return wf
    return None


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

    Delegates to :class:`WorkflowLoadContext` so the entry path shares the
    exact fetch-on-miss policy with the exit path (issue #675).
    """
    ctx = WorkflowLoadContext(
        workflows_dir, local_workflows_dir, scaffold_branch, fetch_cache,
    )
    child = ctx.load(ref)
    if child is None:
        print(f"Warning: Sub-workflow '{ref}' not found", file=sys.stderr)
    return child


def _position_path_str(nodes: list) -> str:
    """Flat outermost-first mount-chain path (issue #618).

    ``name/step|name/step|...`` over a chain produced by
    :func:`_build_position_chain`. The same-agent guard compares this string
    (``WORKFLOW_POSITION_PATH``) between the trigger's posted state and the
    resolved next run: ``step_1`` -> ``step_2`` re-enters the *same* leaf
    (``dev-review-loop/implement``), so only the full chain distinguishes a
    legitimate advance from a true self loop.
    """
    if not nodes:
        return ""
    return "|".join(f"{name}/{step}" for name, _, step in nodes)


def _position_pair(nodes: list) -> dict:
    """Emission pair over a position chain (issue #618).

    Returns ``{WORKFLOW_POSITION, WORKFLOW_POSITION_PATH}`` with empty values
    for an empty chain, so callers can merge the dict unconditionally
    (``result | pair``) without per-field conditionals.
    """
    position_json = _position_to_json(nodes)
    return {
        "WORKFLOW_POSITION": position_json,
        "WORKFLOW_POSITION_PATH": _position_path_str(nodes) if position_json else "",
    }


def _position_pair_from_value(position: Optional["WorkflowPosition"]) -> dict:
    """Emission pair for a resolved ``WorkflowPosition`` (issue #812).

    Same shape as :func:`_position_pair`, but built from the carried-forward
    value object rather than a reconstructed ``(name, ref, step)`` chain.
    ``None``/empty maps to the empty pair so callers merge unconditionally.
    """
    if position is None or not position.refs:
        return {"WORKFLOW_POSITION": "", "WORKFLOW_POSITION_PATH": ""}
    return {
        "WORKFLOW_POSITION": json.dumps(
            position.to_state_dict(), separators=(",", ":")
        ),
        "WORKFLOW_POSITION_PATH": position.path_str(),
    }


def _position_to_json(nodes: list) -> str:
    """Serialize a position chain to single-line JSON (issue #638, v3).

    ``nodes`` is an outermost-first list of ``(workflow_name, ref,
    step_name)`` tuples (see :func:`_build_position_chain`). The chain is
    rendered as the flat v3 state block — ``{"version": 3, "refs": [...],
    "workflow": <leaf>, "step": <leaf>}`` — mirroring
    ``WorkflowPosition.to_state_dict`` so the posted ``WORKFLOW_STATE``
    carries the full mount chain (the #617 dead-end hid the sub-workflow
    behind a single flat ``step``). Single-line so it survives the
    ``KEY=value`` resolver output contract.
    """
    if not nodes:
        return ""
    block = WorkflowPosition(refs=[
        StepRef(workflow=name, step=step) for name, _, step in nodes
    ]).to_state_dict()
    return json.dumps(block, separators=(",", ":"))


def _select_named_or_first(
    wf: WorkflowDefinition, tail_path: Optional[StepPath]
) -> Optional[WorkflowStep]:
    """Entry step of ``wf`` for a nested descent (issue #822).

    When ``tail_path`` names further segments, the next step is the one
    matching its first segment (``None`` when absent — the caller fails
    loudly); otherwise the workflow's first step (today's behavior).
    """
    if not wf.steps:
        return None
    if tail_path is not None and tail_path.segments:
        name = tail_path.segments[0]
        return next((s for s in wf.steps if s.name == name), None)
    return wf.steps[0]


def _advance_path(tail_path: Optional[StepPath]) -> Optional[StepPath]:
    """Drop the consumed segment of ``tail_path`` (issue #822).

    Returns the remaining descent path, or ``None`` once every segment has
    been consumed — after which descent continues through the child's first
    step, exactly like a plain single-mount entry.
    """
    if tail_path is None or len(tail_path.segments) <= 1:
        return None
    return tail_path.model_copy(update={"segments": tail_path.segments[1:]})


def _leaf_lookup_error(wf: WorkflowDefinition, pending: Optional[StepPath]) -> "SubWorkflowLeafError":
    """Fail-loud diagnostic for a missing nested target step (issue #822)."""
    if pending is not None and pending.segments:
        return SubWorkflowLeafError(
            f"step '{pending.segments[0]}' not found in sub-workflow '{wf.name}'"
        )
    return SubWorkflowLeafError(f"Sub-workflow '{wf.name}' has no steps")


def _build_position_chain(
    start_ref: str,
    start_name: str,
    start_step: str,
    fetch_cache: dict,
    visited: set,
    workflows_dir: str,
    scaffold_branch: Optional[str],
    local_workflows_dir: Optional[str] = None,
    start_path: str = "",
    tail_path: Optional[StepPath] = None,
) -> list:
    """Build the mount-chain position for a sub-workflow entry (issue #618).

    Starts at the outer parent node ``(start_name, start_step)`` and
    descends through the child's first steps (reusing the loader and the
    shared ``fetch_cache``) until an agent-bearing leaf. Returns a list of
    ``(workflow_name, workflow_path, step_name)`` tuples, outermost first.
    ``start_path`` (the caller-known file path of the outer workflow) is
    recorded on the outermost node; child nodes always carry the loader-
    resolved reference of the mount that reached them. Cycles/depth reuse
    the ``visited`` name set plus ``_MAX_SUB_WORKFLOW_DEPTH``; an
    unresolvable reference or entryless child stops the chain at the node
    already built.

    ``tail_path`` (issue #822) selects a nested entry: each consumer
    descends to the segment it names rather than the child's first step, so
    the recorded position is the addressed chain (e.g.
    ``tmp-impl/step_3 -> dev-review-loop/review``).
    """
    chain: list = [(start_name, start_path, start_step)]
    ref = start_ref
    while True:
        if not ref or ref in visited:
            break
        if len(chain) > _MAX_SUB_WORKFLOW_DEPTH:
            break
        child = _load_child_workflow(
            ref, workflows_dir, scaffold_branch, fetch_cache,
            local_workflows_dir,
        )
        if child is None or not child.steps:
            break
        if child.name in visited:
            break
        visited.add(child.name)
        leaf = _select_named_or_first(child, tail_path)
        if leaf is None:
            # Unknown nested target — leaf resolution reports the failure.
            break
        chain.append((child.name, ref, leaf.name))
        ref = leaf.workflow or leaf.path or ""
        if leaf.agent or leaf.script is not None or not ref:
            break
        tail_path = _advance_path(tail_path)
    return chain


def _resolve_outer_path(
    name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
) -> str:
    """Best-effort path of an outer workflow referenced by name (issue #618).

    Used to backfill the outermost node of a rebuilt position chain; the
    reference is already proven (it loaded earlier in the same resolution),
    so an empty result is a pure diagnostic gap, never a failure.
    """
    if not name:
        return ""
    if is_path_like_reference(name):
        try:
            return resolve_workflow_reference(name, workflows_dir)
        except ValueError:
            return ""
    try:
        return str(load_workflow_with_dirs(name, workflows_dir, local_workflows_dir or ""))
    except (FileNotFoundError, ValueError):
        return ""


def _step_mount_ref(
    workflow_name: str,
    step_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
) -> str:
    """Mount reference of ``step_name`` in ``workflow_name`` (issue #618).

    The recorded parent pair is ``workflow/step`` — not a loadable
    reference — so the step's own ``workflow``/``path`` field must be
    looked up to continue the chain descent. Empty when the workflow or
    step cannot be loaded (the chain then stops at the parent node).
    """
    try:
        resolved = load_workflow_with_dirs(workflow_name, workflows_dir, local_workflows_dir or "")
        wf = load_workflow(resolved)
    except (FileNotFoundError, ValueError):
        return ""
    step = _find_workflow_step(wf, step_name)
    if step is None:
        return ""
    return step.workflow or step.path or ""


def build_workflow_position_value(
    leaf_name: str,
    leaf_step: str,
    parent_workflow: str,
    parent_step: str,
    workflows_dir: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
) -> Optional[WorkflowPosition]:
    """The ``WorkflowPosition`` that :func:`build_workflow_position` serializes.

    ``None`` when no leaf is resolvable at all. A top-level run (no parent
    pair) yields the bare single-ref position (issue #646); a nested run
    rebuilds the mount chain by descending from the recorded parent pair
    (issue #618). Exposed as a value (issue #812) so the carry-forward
    emission path can splice the rebuilt tail beneath the prior run's
    position instead of re-deriving the whole stack from lossy flat data.
    """
    if not leaf_name:
        return None
    if not parent_workflow:
        # Top-level run (issue #646): the bare single-ref position is the
        # run's canonical address — never empty.
        return WorkflowPosition.bare(leaf_name, leaf_step)
    fetch_cache: dict = {}
    # Anchor the chain on the parent pair (outermost recorded level) and
    # descend first steps until the leaf workflow/step pair is reached.
    # The descent ref is the parent *step's* mount reference — a ``wf/step``
    # composite is not a loadable workflow reference, so it must be looked
    # up in the parent workflow itself.
    chain = _build_position_chain(
        _step_mount_ref(parent_workflow, parent_step, workflows_dir, local_workflows_dir),
        parent_workflow, parent_step,
        fetch_cache, {parent_workflow},
        workflows_dir, scaffold_branch,
        local_workflows_dir,
        start_path=_resolve_outer_path(parent_workflow, workflows_dir, local_workflows_dir),
    )
    if chain and chain[-1][0] == leaf_name and chain[-1][2] == leaf_step:
        pass  # leaf already recorded — nothing to append
    elif chain and chain[-1][0] == leaf_name:
        # Leaf workflow reached but the step differs (re-entry, e.g.
        # ``step_2: workflow: dev-review-loop`` where the executed step is
        # ``implement``, not the child's first step) — substitute.
        chain[-1] = (leaf_name, chain[-1][1], leaf_step)
    else:
        # The recorded parent pair is further out than the leaf (the flat
        # state can carry the outermost level while the run executes a
        # deeper level): append the leaf as the innermost node.
        chain.append((leaf_name, "", leaf_step))
    refs = [StepRef(workflow=name, step=step) for name, _, step in chain]
    return WorkflowPosition(refs=refs) if refs else None


def build_workflow_position(
    leaf_name: str,
    leaf_step: str,
    parent_workflow: str,
    parent_step: str,
    workflows_dir: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
) -> dict:
    """Centralized v2 position emission (issue #618, review fix for defect 3).

    Rebuilds the mount chain from the *leaf* the run actually executes,
    walking back up through intermediate first-steps anchored on the
    recorded parent pair, and returns the ``WORKFLOW_POSITION``/
    ``WORKFLOW_POSITION_PATH`` pair. Position is universal (issue #646):
    top-level runs emit the single bare ref; empty strings only when no
    leaf is resolvable at all. The resolver's own entry-path emission is
    kept verbatim when present — this helper exists so the linear steps
    *inside* a sub-workflow (and every other non-entry resolution path)
    post the same full chain instead of ``position=None``.
    """
    return _position_pair_from_value(
        build_workflow_position_value(
            leaf_name, leaf_step, parent_workflow, parent_step, workflows_dir,
            scaffold_branch=scaffold_branch,
            local_workflows_dir=local_workflows_dir,
        )
    )


def _bindings_payload(bindings: dict) -> dict:
    """Serialize an ``InputBinding`` map for the STEP_INPUTS JSON (issue #813)."""
    return {
        name: binding.model_dump(by_alias=True, exclude_none=True)
        for name, binding in bindings.items()
    }


def build_step_inputs(workflow: WorkflowDefinition, step: WorkflowStep) -> str:
    """Serialize a step's declared input wiring for the engine (issue #813).

    ``consumer`` is the step's own ``inputs:`` bindings; ``specs`` is the
    owning workflow's declared input signature, carrying the ``required``
    policy the engine applies when a provider fails. ``frames`` are attached
    later (:func:`attach_call_frames`) once the run's final position is known.
    Empty string when the step declares no inputs, so un-wired steps keep
    their existing resolver output verbatim.
    """
    if not step.inputs:
        return ""
    payload = {
        "consumer": _bindings_payload(step.inputs),
        "specs": {
            spec.name: {"kind": spec.kind, "required": spec.required}
            for spec in workflow.inputs
        },
    }
    return json.dumps(payload, separators=(",", ":"))


def _ref_matches_name(reference: str, workflow_name: str) -> bool:
    """True when a step's ``workflow``/``path`` reference names ``workflow_name``.

    Cheap string test only: an exact name match, a filename match, or a matching
    path stem. A path like ``tmp_impl/workflow.yaml`` whose declared name differs
    from its stem needs the loader fallback in :func:`_mount_step_among`.
    """
    if reference == workflow_name:
        return True
    path = Path(reference)
    return path.stem == workflow_name or path.name in (
        workflow_name,
        f"{workflow_name}.yaml",
        f"{workflow_name}.yml",
    )


def _mount_step_among(
    workflow: WorkflowDefinition,
    inner_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
    scaffold_branch: Optional[str],
) -> Optional[WorkflowStep]:
    """Find the step of ``workflow`` that mounts ``inner_name``.

    The mount chain's *workflow* names are accurate but its intermediate *step*
    names are not (the chain builder follows first steps, not the actual mount),
    so the frame's call step must be recovered from the outer workflow's own
    steps. Falls back to loading each mount candidate when the reference is a
    path whose declared name differs from its filename stem.
    """
    candidates = [s for s in workflow.steps if (s.workflow or s.path)]
    for step in candidates:
        if _ref_matches_name(step.workflow or step.path, inner_name):
            return step
    for step in candidates:
        child = _load_workflow_by_reference(
            step.workflow or step.path, workflows_dir, local_workflows_dir,
            scaffold_branch,
        )
        if child is not None and child.name == inner_name:
            return step
    return None


def derive_call_frames(
    position_json: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
) -> list:
    """Call frames enclosing the current run (issue #813).

    Each consecutive pair in the mount chain ``outer -> inner`` is a call
    boundary: the ``outer`` step's declared ``inputs`` are the frozen call-site
    bindings for the ``inner`` workflow. Frames are listed outermost-first so an
    inner binding can reference an outer one — the kind-preserving lazy
    forwarding that lets a super-parent push a parameter through an arbitrary
    number of layers. Unresolvable frames are skipped (a diagnostic gap, never a
    resolution failure).
    """
    position = (
        WorkflowPosition.from_env(position_json)
        if isinstance(position_json, str)
        else position_json
    )
    if position is None or len(position.refs) < 2:
        return []
    frames: list = []
    for outer, inner in zip(position.refs, position.refs[1:]):
        call_wf = _load_workflow_by_reference(
            outer.workflow, workflows_dir, local_workflows_dir, scaffold_branch,
        )
        if call_wf is None:
            continue
        call_step = _mount_step_among(
            call_wf, inner.workflow, workflows_dir, local_workflows_dir,
            scaffold_branch,
        )
        if call_step is None or not call_step.inputs:
            continue
        frames.append({
            "callee": inner.workflow,
            "call_step": call_step.name,
            "bindings": _bindings_payload(call_step.inputs),
        })
    return frames


def attach_call_frames(
    step_inputs_json: str,
    position_json: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
) -> str:
    """Return ``step_inputs_json`` with the run's call frames attached.

    Only the frames matter here — the consumer/specs payload was built at step
    resolution, but the accurate mount chain is only known once the run's final
    position has been computed (issue #813).
    """
    frames = derive_call_frames(
        position_json, workflows_dir, local_workflows_dir, scaffold_branch,
    )
    if not frames:
        return step_inputs_json
    try:
        payload = json.loads(step_inputs_json)
    except ValueError:
        return step_inputs_json
    payload["frames"] = frames
    return json.dumps(payload, separators=(",", ":"))


# @stub-protected: signature defined by architect — do not modify
@dataclass(frozen=True)
class SubWorkflowLeaf:
    """Typed terminal leaf of a sub-workflow descent (issue #671).

    The descent through ``workflow:``/``path:`` mount steps always lands on
    the runnable leaf where a run actually executes. Carrying the leaf's
    owning workflow (not the mount node), its step name, and the exact
    descent chain (``refs``, outermost-first) lets the router advance from a
    valid anchor instead of the empty-step mount node that stalled the
    ``scaffold -> sub_impl`` transition in issue #648.

    Attributes:
        agent: Agent role that runs the leaf step ("" for a script leaf).
        step: Terminal runnable step name; invariant: never empty.
        workflow: Name of the workflow that owns the leaf step.
        workflow_next: The leaf workflow's next step after ``step``.
        skill: Merged skills string for the leaf step.
        prompt: Inline prompt for the leaf step ("" when none).
        prompt_replace: True when the leaf step's inline prompt replaces the
            role prompt (issue #784).
        refs: Descent chain, outermost-first; invariant:
            ``refs[-1] == {"workflow": workflow, "step": step}``.
        kind: Leaf kind, ``"agent"`` or ``"script"`` — a sub-workflow may
            descend to a deterministic ``script:`` step (issue #541), which
            must stay representable under this contract.
        script_path: Script to run for a ``"script"`` leaf ("" otherwise).
        script_args: JSON array of the script's argv (issue #974).
        script_working_dir: Repo-relative working directory (issue #974).
        script_env: JSON object of extra environment variables (issue #974).
        script_timeout: Script timeout in minutes for a ``"script"`` leaf.
        complexity: Declared complexity tier of the leaf step ("" when unset).
    """

    agent: str  # @stub-protected
    step: str
    workflow: str
    workflow_next: str  # @stub-protected
    skill: str  # @stub-protected
    prompt: str  # @stub-protected
    refs: tuple[dict[str, str], ...]
    prompt_replace: bool = False  # @stub-protected
    kind: str = "agent"  # @stub-protected
    script_path: str = ""  # @stub-protected
    script_args: str = "[]"  # @stub-protected
    script_working_dir: str = ""  # @stub-protected
    script_env: str = "{}"  # @stub-protected
    script_timeout: Optional[float] = None  # @stub-protected
    # Declared complexity tier of the leaf step (issue #787); "" when unset.
    complexity: str = ""  # @stub-protected


# @stub-protected: signature defined by architect — do not modify
@dataclass(frozen=True)
class SubWorkflowLeafError:
    """Typed failure of a sub-workflow leaf descent (issue #671).

    Returned in place of a :class:`SubWorkflowLeaf` when the referenced child
    cannot be loaded, the descent cycles or exceeds
    ``_MAX_SUB_WORKFLOW_DEPTH``, or the terminal step has no runnable source.
    Consumers map ``reason`` onto their own failure surface so a declared
    mount is surfaced rather than silently stepped over: the runtime entry
    path returns ``SKIP_AGENT`` and the trigger-side marker builder returns
    ``SKIP_AGENT`` with the same reason.

    Attributes:
        reason: Human-readable failure reason.
    """

    reason: str  # @stub-protected


# Result of a sub-workflow leaf descent: a runnable leaf or a typed error.
SubWorkflowResolution = Union[SubWorkflowLeaf, SubWorkflowLeafError]


# @stub-protected: signature defined by architect — do not modify
def _resolve_entry_leaf_for_step(
    step: WorkflowStep,
    workflows_dir: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
) -> SubWorkflowResolution:
    """Resolve the runnable leaf a sub-workflow entry step descends to.

    Shared by the runtime entry path and the trigger-side marker builders
    (issue #609) so all entry routes agree. Loads the referenced child and
    descends to the innermost runnable leaf, returning the typed
    :class:`SubWorkflowLeaf` for that leaf or a :class:`SubWorkflowLeafError`
    when it cannot be resolved (issue #671).
    """
    ref = step.workflow or step.path
    if not ref:
        return SubWorkflowLeafError(
            f"step '{step.name}' has no sub-workflow reference"
        )
    fetch_cache: dict = {}
    child = _load_child_workflow(
        ref, workflows_dir, scaffold_branch, fetch_cache,
        local_workflows_dir=local_workflows_dir,
    )
    if child is None:
        return SubWorkflowLeafError(
            f"Sub-workflow not found: {ref} "
            f"(entered via step '{step.name}'; "
            f"{'no scaffold_branch in state'
               if not scaffold_branch else f'scaffold_branch {scaffold_branch}'})"
        )
    if not child.steps:
        return SubWorkflowLeafError(f"Sub-workflow '{child.name}' has no steps")
    return _resolve_sub_workflow_leaf(
        child, step, workflows_dir, scaffold_branch, fetch_cache, {child.name},
        parent_skill=step.skill,
        local_workflows_dir=local_workflows_dir,
    )


# @stub-protected: signature defined by architect — do not modify
def _resolve_sub_workflow_leaf(
    child_wf: WorkflowDefinition,
    entry_step: WorkflowStep,
    workflows_dir: str,
    scaffold_branch: Optional[str],
    fetch_cache: dict,
    visited: set,
    parent_skill: Optional[list] = None,
    local_workflows_dir: Optional[str] = None,
    tail_path: Optional[StepPath] = None,
) -> SubWorkflowResolution:
    """Resolve the innermost runnable leaf of a (possibly nested) child.

    Descends through child first-steps that are themselves ``workflow:``/
    ``path:`` sub-workflow references until a runnable leaf is found (an
    ``agent`` step, or a deterministic ``script`` step). A child whose first
    step has no runnable source is a leaf in its own right and is surfaced as
    an error (issue #609).

    ``tail_path`` (issue #822) addresses a non-first step: each level
    descends to the segment it names (``step_3``, then ``review`` …) instead
    of the child's first step, so a nested selector can re-enter a
    scaffolded plan at an arbitrary depth. An unknown segment fails loudly
    rather than silently falling back to the first step.

    Two guards bound the descent (review round 2): a *cycle* guard keyed on
    the loaded workflow's canonical ``name`` (plus a pre-load check on the
    reference stem, so a path-based self/cycle is caught before its fetch),
    and a *max-depth* bound (``_MAX_SUB_WORKFLOW_DEPTH``) so a deep
    non-cyclic chain terminates instead of unbounded descent.

    Returns the typed :class:`SubWorkflowLeaf` for the terminal runnable
    leaf, or a :class:`SubWorkflowLeafError` when no runnable leaf can be
    resolved; an empty terminal step is an explicit error rather than a
    silently-returned mount node (issue #671).
    """
    entry_ref = entry_step.workflow or entry_step.path or ""
    wf = child_wf
    pending = tail_path
    leaf = _select_named_or_first(wf, pending)
    if leaf is None:
        return _leaf_lookup_error(wf, pending)
    refs: list[dict[str, str]] = [{"workflow": wf.name, "step": leaf.name}]
    depth = 0
    while leaf is not None and not leaf.agent and leaf.script is None:
        # A first-step with no runnable source is itself a leaf: surface it.
        if not leaf.workflow and not leaf.path and not leaf.repo:
            break
        ref = leaf.workflow or leaf.path
        if not ref:
            break
        if _workflow_ref_stem(ref) in visited:
            return SubWorkflowLeafError(
                f"Sub-workflow cycle detected at '{ref}' "
                f"(entered via '{entry_ref}')"
            )
        if depth >= _MAX_SUB_WORKFLOW_DEPTH:
            return SubWorkflowLeafError(
                f"max sub-workflow nesting depth ({_MAX_SUB_WORKFLOW_DEPTH}) "
                f"exceeded at '{ref}' (entered via '{entry_ref}')"
            )
        child = _load_child_workflow(
            ref, workflows_dir, scaffold_branch, fetch_cache,
            local_workflows_dir,
        )
        if child is None:
            return SubWorkflowLeafError(
                f"Sub-workflow not found: {ref} (nested under '{entry_ref}')"
            )
        if child.name in visited:
            return SubWorkflowLeafError(
                f"Sub-workflow cycle detected at '{child.name}' "
                f"(entered via '{entry_ref}')"
            )
        visited.add(child.name)
        depth += 1
        if not parent_skill and leaf.skill:
            parent_skill = leaf.skill
        wf = child
        pending = _advance_path(pending)
        leaf = _select_named_or_first(wf, pending)
        if leaf is None:
            return _leaf_lookup_error(wf, pending)
        refs.append({"workflow": wf.name, "step": leaf.name})

    if leaf is None or not leaf.name:
        return SubWorkflowLeafError(
            f"Sub-workflow '{wf.name}' has no runnable leaf step "
            f"(entered via '{entry_ref}')"
        )

    merged_skill = _skills_to_string(parent_skill or leaf.skill)
    workflow_next = wf.steps[1].name if len(wf.steps) > 1 else ""

    if leaf.agent:
        return SubWorkflowLeaf(
            agent=leaf.agent,
            step=leaf.name,
            workflow=wf.name,
            workflow_next=workflow_next,
            skill=merged_skill,
            prompt=leaf.prompt or "",
            prompt_replace=bool(leaf.prompt_replace),
            refs=tuple(refs),
            complexity=leaf.complexity or "",
        )

    if leaf.script is not None:
        return SubWorkflowLeaf(
            agent="",
            step=leaf.name,
            workflow=wf.name,
            workflow_next=workflow_next,
            skill=merged_skill,
            prompt=leaf.prompt or "",
            prompt_replace=bool(leaf.prompt_replace),
            refs=tuple(refs),
            kind="script",
            script_path=leaf.script.command or "",
            script_args=leaf.script.to_process_env()["SCRIPT_ARGS"],
            script_working_dir=leaf.script.working_dir or "",
            script_env=leaf.script.to_process_env()["SCRIPT_ENV"],
            script_timeout=leaf.script.timeout_minutes,
            complexity=leaf.complexity or "",
        )

    return SubWorkflowLeafError(
        f"Sub-workflow '{wf.name}' step '{leaf.name}' has no runnable source "
        f"(no agent or script; entered via '{entry_ref}')"
    )


def _resolve_sub_workflow_entry(
    current_step: WorkflowStep,
    workflow: WorkflowDefinition,
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
    outer_parent_workflow: str = "",
    outer_parent_step: str = "",
    local_workflows_dir: Optional[str] = None,
    step_path: Optional[StepPath] = None,
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
    # step may itself reference a (further nested) sub-workflow. A nested
    # selector (issue #822) walks ``step_path`` instead of the first step.
    leaf = _resolve_sub_workflow_leaf(
        child_wf, current_step, workflows_dir, scaffold_branch,
        fetch_cache, {child_wf.name},
        parent_skill=current_step.skill,
        local_workflows_dir=local_workflows_dir,
        tail_path=step_path,
    )
    if isinstance(leaf, SubWorkflowLeafError):
        return {"SKIP_AGENT": "true", "REASON": leaf.reason}

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

    # Full mount-chain address (issue #618): the leaf pair above is one level;
    # the v2 position records the whole chain (outermost -> leaf) so the
    # posted WORKFLOW_STATE reveals the sub-workflow the run executes in.
    # ``target_path`` is the loader-resolved file path of the mount (local,
    # fetched, or cloned), so the chain records actual paths, not guesses.
    position = _position_pair(_build_position_chain(
        target_path, workflow.name, current_step.name,
        fetch_cache, {workflow.name},
        workflows_dir, scaffold_branch,
        local_workflows_dir=local_workflows_dir,
        tail_path=step_path,
    ))

    if leaf.kind == "script":
        return {
            "ROLE_NAME": "",
            "RUN_SCRIPT": "true",
            "SCRIPT_PATH": leaf.script_path,
            "SCRIPT_ARGS": leaf.script_args,
            "SCRIPT_WORKING_DIR": leaf.script_working_dir,
            "SCRIPT_ENV": leaf.script_env,
            "SCRIPT_TIMEOUT_MINUTES": str(leaf.script_timeout or ""),
            "WORKFLOW_NAME": leaf.workflow,
            "WORKFLOW_STEP": leaf.step,
            "PARENT_WORKFLOW": parent_wf_name,
            "PARENT_WORKFLOW_STEP": parent_step_name,
            "STEP_SKILLS": leaf.skill,
            "STEP_SKILL_SPECS": _skill_specs_from_names(leaf.skill),
            "STEP_PROMPT_FILE": _write_step_prompt_file(leaf.prompt),
            "STEP_COMPLEXITY": leaf.complexity,
            "STEP_PROMPT_REPLACE": "",
            "SKIP_AGENT": "true",
            "REASON": f"step '{leaf.step}' in sub-workflow '{leaf.workflow}' is a script step",
        } | position

    prompt_file = TASK_TO_PROMPT.get(leaf.agent)
    if not prompt_file:
        return {
            "SKIP_AGENT": "true",
            "REASON": (
                f"Unknown agent '{leaf.agent}' in sub-workflow "
                f"'{leaf.workflow}' (step '{leaf.step}')"
            ),
        }

    return {
        "ROLE_NAME": leaf.agent,
        "PROMPT_LOCATION": f"{prompt_base}/{prompt_file}",
        "WORKFLOW_NAME": leaf.workflow,
        "WORKFLOW_STEP": leaf.step,
        "WORKFLOW_NEXT": leaf.workflow_next,
        "PARENT_WORKFLOW": parent_wf_name,
        "PARENT_WORKFLOW_STEP": parent_step_name,
        "STEP_SKILLS": leaf.skill,
        "STEP_SKILL_SPECS": _skill_specs_from_names(leaf.skill),
        "STEP_PROMPT_FILE": _write_step_prompt_file(leaf.prompt),
        "STEP_COMPLEXITY": leaf.complexity,
        "STEP_PROMPT_REPLACE": _prompt_replace_flag(leaf.prompt_replace),
        "SKIP_AGENT": "false",
    } | position


def resolve_sub_workflow_entry(
    current_step: WorkflowStep,
    workflow: WorkflowDefinition,
    workflows_dir: str,
    prompt_base: str,
    scaffold_branch: Optional[str] = None,
    local_workflows_dir: Optional[str] = None,
    step_path: Optional[StepPath] = None,
) -> Optional[dict]:
    """Public entry point for sub-workflow entry resolution.

    Delegates to :func:`_resolve_sub_workflow_entry` (same contract) so that
    other resolution paths (e.g. ``/nebuko force-next-step``, issue #599)
    can force-enter a sub-workflow step with exactly the same child-loading
    and parent-reference logic as the normal happy path. ``scaffold_branch``
    enables fetch-on-miss for branch-mounted children (issue #604).
    ``step_path`` (issue #822) descends to a nested non-first step instead of
    the child's first step.
    """
    return _resolve_sub_workflow_entry(
        current_step, workflow, workflows_dir, prompt_base, scaffold_branch,
        local_workflows_dir=local_workflows_dir,
        step_path=step_path,
    )


def _parent_chain_from_history(
    history,
    current_workflow: str,
    recorded_parent_wf: str,
    recorded_parent_step: str,
) -> list[tuple[str, str]]:
    """Recover the ancestor pairs (immediate→outermost) from state history.

    The flat parent-pair state records the outermost level only (issue #609
    "prefer-outer" entry design), so in a 3-level chain
    (``plan-orchestration -> tmp-impl -> dev-review-loop``) the intermediate
    level (``tmp-impl/step_N``) is not on record. The history carries the full
    position stack per executed step (issue #638); the stack below the current
    workflow is that entry's ancestor chain, ordered from the immediate parent
    outward. The most recent matching entry wins, and only stacks that still
    carry the recorded pair are trusted, so a stale run of the same reusable
    child under a different parent is ignored.
    """
    if not history or not current_workflow:
        return []
    if not (recorded_parent_wf and recorded_parent_step):
        return []
    recorded = (recorded_parent_wf, recorded_parent_step)
    chain: list[tuple[str, str]] = []
    for entry in history:
        stack = list(getattr(entry, "position", None) or [])
        if len(stack) < 2 or stack[-1].workflow != current_workflow:
            continue
        if not any((ref.workflow, ref.step) == recorded for ref in stack):
            continue
        chain = [(ref.workflow, ref.step) for ref in reversed(stack[:-1])]
    return chain


def _load_workflow_by_name(
    workflow_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
    fetch_cache: Optional[dict] = None,
) -> Optional[WorkflowDefinition]:
    """Load a workflow by its declared ``name`` via a recursive file scan.

    The state history records a workflow's declared name (issue #657), which
    :func:`_load_workflow_by_reference` cannot resolve when the workflow is
    mounted only through a path-like reference — e.g. a branch-mounted
    ``tmp_impl/workflow.yaml`` declaring ``name: tmp-impl``. Scans the same
    directories (and subdirectories) as :func:`_find_outer_parent`. Returns
    ``None`` when no file declares that name.

    Fetch-on-miss (issue #675): when the name resolves only on the state's
    ``scaffold_branch``, :class:`WorkflowLoadContext` fetches it, so the exit
    path honors the same policy as the entry path.
    """
    return WorkflowLoadContext(
        workflows_dir, local_workflows_dir, scaffold_branch, fetch_cache,
    ).load_by_name(workflow_name)


def _return_marker_for_parent(
    parent_pair: tuple[str, str],
    branch_key: Optional[str],
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
    load_ctx: Optional[WorkflowLoadContext] = None,
) -> Optional[dict]:
    """Return-to-parent marker anchored on one nesting level (issue #657).

    The flat parent-pair state records the outermost level only (issue #609
    prefer-outer design), so for a nested child (``outer -> mid -> leaf``) the
    recorded pair skips the intermediate mount — the exit would resume the
    outer level instead of advancing the plan one step. Anchored on the level
    that actually mounted the child, the mount step's ``next`` is honoured
    instead: a plain ``next`` resumes in that workflow, and a sub-workflow
    ``next`` re-enters the child carrying the anchor pair as the new parent
    (issue #609 descent). Returns ``None`` when the level cannot be loaded or
    its step is terminal, so the caller unwinds one level further.

    The anchor level is loaded through ``load_ctx`` (issue #675), so a
    branch-mounted intermediate level (``tmp-impl``) is fetched instead of
    being skipped — the missing level is what collapsed the unwind to the
    outer step.
    """
    parent_wf_name, parent_step_name = parent_pair
    ctx = load_ctx or WorkflowLoadContext(
        workflows_dir, local_workflows_dir, scaffold_branch, {},
    )
    parent_wf = ctx.load(parent_wf_name) or ctx.load_by_name(parent_wf_name)
    if parent_wf is None:
        return None
    parent_step = _find_workflow_step(parent_wf, parent_step_name)
    if parent_step is None:
        return None
    target_name = _match_condition(parent_step, branch_key or "")
    if target_name is None:
        target_name = parent_step.next
    if target_name is None:
        return None
    target_step = _find_workflow_step(parent_wf, target_name)
    if target_step is None:
        return None
    if target_step.workflow or target_step.path or target_step.repo:
        # The anchor's next step mounts the child again: re-enter it with the
        # anchor pair as the recorded parent (issue #609 descent).
        marker = _sub_workflow_entry_marker(
            target_step.workflow or target_step.path or target_step.repo,
            parent_wf, target_step, workflows_dir, local_workflows_dir,
            scaffold_branch,
        )
        if marker is None:
            return None
        if marker.get("SKIP_AGENT") == "true":
            # Child re-entry failed: surface the typed failure unchanged
            # rather than decorating it as a successful entry marker.
            return marker
    else:
        marker = {
            "exited_sub_workflow": True,
            "task": target_step.agent,
            "step": target_step.name,
            "workflow": parent_wf.name,
        }
    effective_branch = _branch_key_for(parent_step, branch_key or "")
    if effective_branch:
        marker["branch"] = effective_branch
    return marker


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
            "SCRIPT_PATH": next_step.script.command,
            **next_step.script.to_process_env(),
            "SCRIPT_TIMEOUT_MINUTES": str(next_step.script.timeout_minutes or ""),
            "WORKFLOW_NAME": cur_wf.name,
            "WORKFLOW_STEP": next_step_name,
            "WORKFLOW_NEXT": next_step.next or "",
            "PARENT_WORKFLOW": parent_workflow,
            "PARENT_WORKFLOW_STEP": parent_step,
            "STEP_SKILLS": _skills_to_string(next_step.skill),
            "STEP_SKILL_SPECS": _skill_specs_json(next_step.skill),
            "STEP_PROMPT_FILE": "",
            "STEP_COMPLEXITY": next_step.complexity or "",
            "STEP_PROMPT_REPLACE": "",
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
        "STEP_SKILL_SPECS": _skill_specs_json(next_step.skill),
        "STEP_PROMPT_FILE": _write_step_prompt_file(next_step.prompt),
        "STEP_COMPLEXITY": next_step.complexity or "",
        "STEP_PROMPT_REPLACE": _prompt_replace_flag(next_step.prompt_replace),
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
    load_ctx: Optional[WorkflowLoadContext] = None,
) -> Optional[dict]:
    """Resolve returning to parent workflow after sub-workflow completion.

    Loads the parent workflow, finds the step that was the sub-workflow entry
    point, and resolves the next step after it using conditional routing.
    When that next step is itself a sub-workflow reference (issue #609, e.g.
    ``tmp-impl``'s ``step_2: workflow: dev-review-loop``), the resolution
    re-enters it via :func:`_next_step_resolution` so the scaffold's
    per-step plan advances one loop at a time.

    The parent is loaded through ``load_ctx`` (issue #675): a branch-mounted
    parent level (``tmp-impl``) unresolved locally is fetched, matching the
    entry-path fetch-on-miss policy.
    """
    # Load parent workflow — supports names and repo-root-relative paths
    # (issue #468): path-like references resolve outside workflows/.
    # Plain names honor consumer-local precedence (issue #529).
    load_ctx = load_ctx or WorkflowLoadContext(
        workflows_dir, local_workflows_dir, scaffold_branch, {},
    )
    parent_wf = (
        load_ctx.load(parent_workflow_name)
        or load_ctx.load_by_name(parent_workflow_name)
    )
    if parent_wf is None:
        print(f"Warning: Parent workflow '{parent_workflow_name}' not found",
              file=sys.stderr)
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Parent workflow '{parent_workflow_name}' not found",
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
                load_ctx=load_ctx,
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
            "STEP_COMPLEXITY": "",
            "STEP_PROMPT_REPLACE": "",
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
            "STEP_COMPLEXITY": "",
            "STEP_PROMPT_REPLACE": "",
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
        "STEP_SKILL_SPECS": _skill_specs_json(current_step.skill),
        "STEP_PROMPT_FILE": _write_step_prompt_file(current_step.prompt),
        "STEP_COMPLEXITY": current_step.complexity or "",
        "STEP_PROMPT_REPLACE": _prompt_replace_flag(current_step.prompt_replace),
        "SKIP_AGENT": "false",
    }


def _load_workflow_by_reference(
    workflow_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
    fetch_cache: Optional[dict] = None,
) -> Optional[WorkflowDefinition]:
    """Load a workflow by name or repo-root-relative path reference.

    Plain names are scanned with the consumer-local directory taking
    precedence (issue #529); path-like references resolve as before.

    Fetch-on-miss (issue #675): a branch-mounted reference absent from the
    default-branch checkout is fetched from ``scaffold_branch`` via
    :class:`WorkflowLoadContext`, so the exit path matches the entry path.
    """
    return WorkflowLoadContext(
        workflows_dir, local_workflows_dir, scaffold_branch, fetch_cache,
    ).load(workflow_name)


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
    carrying ``task=None``. The recorded ``workflow``/``step`` pair is the
    terminal leaf the run actually executes (``workflow`` = the leaf's owning
    workflow, never the mount reference) so the router has a valid anchor to
    advance from — recording the mount node produced the empty-step stall of
    issue #648. When the child cannot be loaded or has no runnable leaf, the
    typed :class:`SubWorkflowLeafError` is surfaced as
    ``{"SKIP_AGENT": "true", "REASON": ...}`` (the codebase-wide failure
    convention) rather than ``None``; ``None`` is reserved for "not a mount",
    so a declared mount is never silently stepped over (issue #671).

    Invariant (trigger vs. runtime symmetry): this marker records
    ``workflow`` = the innermost leaf workflow, matching the runtime entry
    path (``_resolve_sub_workflow_entry``), which records ``WORKFLOW_NAME`` =
    that same leaf. Both must stay consistent in descent (same leaf, same
    skill merge); a change to either side must preserve this invariant
    (pinned by ``test_trigger_side_marker_matches_leaf``).
    """
    leaf = _resolve_entry_leaf_for_step(
        parent_step, workflows_dir, scaffold_branch, local_workflows_dir
    )
    if isinstance(leaf, SubWorkflowLeafError):
        print(f"Warning: {leaf.reason}", file=sys.stderr)
        return {"SKIP_AGENT": "true", "REASON": leaf.reason}
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
        "task": leaf.agent if leaf.kind == "agent" else "",
        "step": leaf.step,
        "workflow": leaf.workflow,
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


def _enclosing_mount_refs(
    chain: list[tuple[str, str]], workflows_dir: str
) -> dict[str, str]:
    """Map each ancestor level's declared name to its branch-mount reference.

    ``chain`` is ordered immediate→outermost, so level ``i`` is mounted by
    level ``i + 1``'s step; that step's declared ``workflow``/``path``
    reference is the branch-checkout location of the level (issue #812) — a
    branch-mounted child's declared ``name`` (``tmp-impl``) is not its
    filename (``tmp_impl/workflow.yaml``), so a by-name lookup misses it. The
    outermost level has no enclosing step (it lives in the default checkout)
    and is left to by-name resolution. Later levels never overwrite an
    earlier hint, keeping the innermost mount authoritative.
    """
    refs: dict[str, str] = {}
    for i, (name, _step) in enumerate(chain):
        if i + 1 >= len(chain):
            break
        outer_wf, outer_step = chain[i + 1]
        ref = _parent_step_child_reference(outer_wf, outer_step, workflows_dir)
        if ref and name not in refs:
            refs[name] = ref
    return refs


def _return_to_parent_on_terminal(
    parent_workflow: str,
    parent_workflow_step: str,
    branch_result: Optional[str],
    workflows_dir: str,
    agent_hint: Optional[str],
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
    history: Optional[list] = None,
    current_workflow: str = "",
) -> Optional[dict]:
    """Exit to the parent when a sub-workflow cannot advance internally.

    Used when the current step is terminal (``next: null``) or its target
    step is missing. Returns the parent's next step after the sub-workflow
    reference, or falls back to ``agent_hint`` / ``None`` when no parent is
    set (overall terminal).

    When ``history`` reveals ancestor levels deeper than the recorded
    (outermost) pair, the exit unwinds from the level that actually mounted
    the child (issue #657): each level's ``next`` is honoured in turn, so a
    nested plan advances one step at a time and only falls back to the
    outermost successor once no deeper level has a target. The recorded-pair
    marker stays the fallback.
    """
    if parent_workflow and parent_workflow_step:
        chain = _parent_chain_from_history(
            history, current_workflow, parent_workflow, parent_workflow_step,
        )
        load_ctx = WorkflowLoadContext(
            workflows_dir, local_workflows_dir, scaffold_branch, {},
            _enclosing_mount_refs(chain, workflows_dir),
        )
        for pair in chain:
            marker = _return_marker_for_parent(
                pair, branch_result, workflows_dir,
                local_workflows_dir, scaffold_branch, load_ctx=load_ctx,
            )
            if marker is not None:
                return marker
        parent_wf = (
            load_ctx.load(parent_workflow) or load_ctx.load_by_name(parent_workflow)
        )
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


def _marker_for_target_step(
    target_step: WorkflowStep,
    workflow: WorkflowDefinition,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
    scaffold_branch: Optional[str],
    branch_key: str = "",
) -> Optional[dict]:
    """Classify a resolved target step into a trigger-side resolution marker.

    A target that mounts a sub-workflow (``workflow``/``path``) descends to the
    child's innermost runnable leaf (issue #657) via the same
    ``_resolve_entry_leaf_for_step`` descent the runtime entry path uses, so a
    linear ``next`` pointing at a mount (e.g. ``scaffold -> sub_impl``) yields
    the leaf's agent instead of ``task=None``. A ``script`` target yields the
    issue #541 script marker; any other target yields the plain
    ``{"task", "step"}`` marker. Returns ``None`` when a mount reference cannot
    be resolved, preserving the clean-failure semantics.

    This is the trigger-side counterpart of the runtime mount descent in
    ``_next_step_resolution``; both classify the mount through the shared
    ``_resolve_entry_leaf_for_step`` so they cannot drift.
    """
    child_ref = _resolve_sub_workflow_ref_target(target_step)
    if child_ref:
        marker = _sub_workflow_entry_marker(
            child_ref, workflow, target_step, workflows_dir,
            local_workflows_dir, scaffold_branch,
        )
        if marker is not None and branch_key:
            marker["branch"] = branch_key
        return marker

    # Deterministic script step (issue #541): distinct marker dict, same
    # pattern as the sub-workflow lifecycle markers — no "task" key because
    # downstream consumers assume a non-None agent name.
    if target_step.script is not None:
        result = {
            "run_script": True,
            "script_path": target_step.script.command,
            "script_args": target_step.script.to_process_env()["SCRIPT_ARGS"],
            "script_working_dir": target_step.script.working_dir or "",
            "script_env": target_step.script.to_process_env()["SCRIPT_ENV"],
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


def _resolve_next_marker(
    workflow_name: str,
    current_step_name: str,
    workflows_dir: str,
    agent_hint: Optional[str] = None,
    branch_result: Optional[str] = None,
    parent_workflow: str = "",
    parent_workflow_step: str = "",
    local_workflows_dir: Optional[str] = None,
    scaffold_branch: Optional[str] = None,
    history: Optional[list] = None,
) -> Optional[Union[dict, _StepGap]]:
    """Legacy marker-dict resolver (implementation detail of #923 retype).

    Same behavior as before: returns the marker dicts documented below, or
    ``None``. Adds one internal sentinel: a :class:`_StepGap` is returned when
    the step's conditions are present, nothing matched, and no linear ``next``
    exists — the config gap that the typed wrapper reports as
    ``RouteKind.UNRESOLVED`` (issue #923) instead of a silent finish.

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
        history: Executed-step history entries; their position stacks let a
            terminal child recover its *immediate* parent level, which the flat
            parent pair does not carry (issue #657).

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
        return _return_to_parent_on_terminal(
            parent_workflow, parent_workflow_step, branch_result, workflows_dir,
            agent_hint, local_workflows_dir, scaffold_branch,
            history=history, current_workflow=workflow.name,
        )

    # Resolve target step name
    target_step_name: Optional[str] = None
    branch_key: Optional[str] = None
    # True once a declared edge was followed — including a matched condition
    # whose ``next`` is ``null`` (a declared finish), so a legitimate terminal
    # is never mistaken for the #923 conditions-gap (issue #923).
    branch_matched = False

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
                    matched = True
                    break
        branch_matched = matched
    elif not branch_result and current_step.conditions and current_step.next is None:
        # Branch-less dispatch on a conditions-only step (issue #618):
        # honor a ``default`` condition instead of dead-ending. Only when the
        # linear ``next`` is also absent, so existing steps with both keep
        # their linear behavior.
        for condition in current_step.conditions:
            if condition.on_result == "default":
                target_step_name = condition.next
                branch_key = "default"
                branch_matched = True
                break
    else:
        # Follow linear next field
        target_step_name = current_step.next
        branch_matched = True

    if target_step_name is None:
        # A conditions step whose edges matched nothing is a config gap, not a
        # declared end (issue #923): report it so the typed wrapper can
        # distinguish it from an honest finish and never route it to the silent
        # WORKFLOW_COMPLETE ping.
        if current_step.conditions and current_step.next is None and not branch_matched:
            return _StepGap(
                step=current_step.name,
                reason=(
                    f"step '{current_step.name}' declares conditions but none "
                    f"matched branch '{branch_result or ''}' and it has no "
                    "linear next"
                ),
            )
        return _return_to_parent_on_terminal(
            parent_workflow, parent_workflow_step, branch_result, workflows_dir, agent_hint,
            local_workflows_dir, scaffold_branch,
            history=history, current_workflow=workflow.name,
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

    # Classify the target into a marker (issue #657): a mount target descends
    # to its leaf like the current-step entry path above, so a linear ``next``
    # pointing at a sub-workflow (e.g. ``scaffold -> sub_impl``) resolves to the
    # child's first runnable agent instead of an empty ``task``.
    return _marker_for_target_step(
        target_step, workflow, workflows_dir, local_workflows_dir,
        scaffold_branch, branch_key or "",
    )


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
    history: Optional[list] = None,
) -> Optional[RouteResolution]:
    """Typed resolver (issue #923): resolve the next step as a RouteResolution.

    Retypes :func:`_resolve_next_marker`'s legacy marker dicts onto
    :class:`RouteResolution` and, crucially, separates three outcomes that the
    legacy ``None`` conflated:

    - :attr:`RouteKind.DECLARED_FINISH` — the workflow explicitly ends here (a
      matched ``on_result`` edge or linear ``next`` with ``next: null``, or a
      parent return with no parent). A benign finish.
    - :attr:`RouteKind.UNRESOLVED` — a conditions step whose edges matched
      nothing and which has no linear ``next``: a config gap that must ping for
      input, never look like an honest finish (the #923 round-2 dead-end).
    - ``None`` — the workflow/step did not resolve at all (missing file, unknown
      step), preserving the clean-failure contract.

    The parameter list is unchanged; callers that still expect the marker dict
    use :func:`resolution_to_marker` (or :meth:`RouteResolution.as_marker`).
    """
    marker = _resolve_next_marker(
        workflow_name,
        current_step_name,
        workflows_dir,
        agent_hint=agent_hint,
        branch_result=branch_result,
        parent_workflow=parent_workflow,
        parent_workflow_step=parent_workflow_step,
        local_workflows_dir=local_workflows_dir,
        scaffold_branch=scaffold_branch,
        history=history,
    )
    if isinstance(marker, _StepGap):
        return RouteResolution(kind=RouteKind.UNRESOLVED, step=marker.step, reason=marker.reason)
    if marker is not None:
        return _marker_to_resolution(marker)
    kind = _classify_terminal_kind(
        workflow_name,
        current_step_name,
        workflows_dir,
        local_workflows_dir,
        branch_result=branch_result,
    )
    if kind is RouteKind.UNRESOLVED:
        return RouteResolution(kind=kind, step=current_step_name)
    if kind is RouteKind.DECLARED_FINISH:
        return RouteResolution(kind=kind, step=current_step_name)
    return None


def _classify_terminal_kind(
    workflow_name: str,
    current_step_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
    branch_result: Optional[str] = None,
) -> Optional[RouteKind]:
    """Classify a ``None`` marker result: DECLARED_FINISH, UNRESOLVED, or None.

    A ``None`` from :func:`_resolve_next_marker` means "no successor". It is a
    *declared finish* when the step exists and honestly has no onward edge
    (conditions all ``next: null``, or a linear ``next: null``), and
    *unresolved* when the step declares forward edges that matched nothing.
    A missing workflow/step stays ``None`` (not a finish).

    ``branch_result`` is the agent's verdict key. When it matches a declared
    ``on_result`` edge whose ``next`` is ``null`` the workflow explicitly ends
    here — the honest finish that must keep pinging ``WORKFLOW_COMPLETE``
    (issue #923). Without it, a matched-null edge is indistinguishable from a
    verdict-less conditions step and both would read as *unresolved*.
    """
    try:
        workflow = _load_workflow_by_reference(
            workflow_name, workflows_dir, local_workflows_dir
        )
    except Exception:
        return None
    if workflow is None:
        return None
    current_step = next(
        (s for s in workflow.steps if s.name == current_step_name), None
    )
    if current_step is None:
        return None
    if current_step.conditions and current_step.next is None:
        matched = next(
            (
                cond
                for cond in current_step.conditions
                if branch_result and cond.on_result == branch_result
            ),
            None,
        )
        if matched is not None:
            return (
                RouteKind.DECLARED_FINISH
                if matched.next is None
                else RouteKind.UNRESOLVED
            )
        # Conditions exist but every reachable edge is ``next: null`` or the
        # key did not match: the internal marker resolver already raised the
        # pure "nothing matched" case as ``_StepGap``; here we only reach a
        # genuine all-null terminal, which is a declared finish.
        if all(cond.next is None for cond in current_step.conditions):
            return RouteKind.DECLARED_FINISH
        return RouteKind.UNRESOLVED
    if current_step.next is None:
        return RouteKind.DECLARED_FINISH
    # A forward edge existed but produced no successor (missing target step).
    return RouteKind.UNRESOLVED


def main():
    import default_paths

    parser = argparse.ArgumentParser(description="Resolve workflow routing for agent")
    parser.add_argument(
        "--workflows-dir",
        default=default_paths.config_dir("workflows", "WORKFLOWS_DIR"),
        help="Path to workflows directory",
    )
    parser.add_argument(
        "--prompt-base",
        default=default_paths.config_dir("prompts", "PROMPT_BASE"),
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
