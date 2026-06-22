#!/usr/bin/env python3
"""
Workflow state management for structured, machine-parseable agent state.

Defines WorkflowState as a Pydantic model and provides utilities to:
- Extract state from GitHub comments (embedded in collapsed <details> sections)
- Build new state by appending history entries
- Serialize state to JSON for embedding in comments
- Format the collapsed section markup for human-readable + machine-readable output

Design:
- Each bot comment starts with a collapsed <details> section containing JSON state.
- State is Strategy 1 (full snapshot) with summarization — latest comment has
  everything; history entries are summarized (1-2 sentences), not raw output.
- Pinned comment IDs reference GitHub comments whose full content should be
  fetched at runtime and injected into context.
- Metrics from the current agent run are embedded in the state.
- The append-only comment stream is preserved — no editing of existing comments.

Usage:
    from state_manager import (
        WorkflowState, HistoryEntry, StateMetrics,
        extract_state, build_state, format_collapsed_section,
    )
"""

from __future__ import annotations

import json
import re
import textwrap
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── MARKERS ────────────────────────────────────────────────────────────

STATE_MARKER_START = "<!-- WORKFLOW_STATE_START -->"
STATE_MARKER_END = "<!-- WORKFLOW_STATE_END -->"


# ── Pydantic Models ────────────────────────────────────────────────────


class HistoryEntry(BaseModel):
    """A summarized record of one workflow step."""

    step: str
    agent: str
    summary: str = ""
    comment_id: Optional[int] = None
    artifacts: list[str] = Field(default_factory=list)
    pin: bool = False


class StateMetrics(BaseModel):
    """Compact metrics embedded in workflow state (subset of Metrics schema)."""

    run_id: str = ""
    model_name: str = ""
    agent_role: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    max_context_window: int = 0
    tool_call_latency_ms: float = 0.0
    agent_runtime_ms: float = 0.0
    nb_tool_calls: int = 0
    tool_call_error_rate: float = 0.0
    review_fix_requests: int = 0

    @staticmethod
    def from_full_metrics_dict(data: dict[str, Any]) -> "StateMetrics":
        """Extract relevant fields from the full Metrics dict."""
        return StateMetrics(
            run_id=data.get("run_id", ""),
            model_name=data.get("model_name", ""),
            agent_role=data.get("agent_role", ""),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            max_context_window=data.get("max_context_window", 0),
            tool_call_latency_ms=data.get("tool_call_latency_ms", 0.0),
            agent_runtime_ms=data.get("agent_runtime_ms", 0.0),
            nb_tool_calls=data.get("nb_tool_calls", 0),
            tool_call_error_rate=data.get("tool_call_error_rate", 0.0),
            review_fix_requests=data.get("review_fix_requests", 0),
        )


class WorkflowState(BaseModel):
    """
    Structured workflow state embedded in GitHub comments.

    Provides typed, machine-parseable state management replacing the previous
    HTML-comment-based phase detection. Every agent step produces a fresh
    full-snapshot state (Strategy 1 + summarization).

    Fields:
        version: Monotonically increasing version number for each state update.
        workflow: Name of the active workflow (e.g., "spec-driven-design").
        step: Current step name within the workflow.
        agent: Agent role executing the current step.
        phase: High-level phase (e.g., "analysis", "planning", "execution").
        history: Summarized records of all prior steps in this workflow run.
        pinned_comment_ids: GitHub comment IDs whose full content should be
            fetched at runtime for high-fidelity context.
        metrics: Metrics from the most recent agent invocation.
        current_context: Free-form context dict for shared data between agents
            (pending tasks, constraints, decisions, etc.).
        parent_workflow: Parent workflow name if currently in a sub-workflow.
        parent_workflow_step: Step in parent that triggered the sub-workflow.
    """

    version: int = 1
    workflow: str = ""
    step: str = ""
    agent: str = ""
    phase: str = ""
    history: list[HistoryEntry] = Field(default_factory=list)
    pinned_comment_ids: list[int] = Field(default_factory=list)
    metrics: Optional[StateMetrics] = None
    current_context: dict[str, Any] = Field(default_factory=dict)
    parent_workflow: Optional[str] = None
    parent_workflow_step: Optional[str] = None


# ── Extraction ─────────────────────────────────────────────────────────

_STATE_JSON_RE = re.compile(
    rf"{re.escape(STATE_MARKER_START)}\s*\n(.*?)\n\s*{re.escape(STATE_MARKER_END)}",
    re.DOTALL,
)


def extract_state(comments: list[dict[str, Any]]) -> Optional[WorkflowState]:
    """
    Extract the latest WorkflowState from a list of GitHub comments.

    Scans comments in reverse chronological order for the first comment
    containing state markers. Returns None if no state is found.

    Args:
        comments: List of comment dicts, each with at least a "body" key.

    Returns:
        Parsed WorkflowState or None if no state found.
    """
    for comment in reversed(comments):
        body = comment.get("body", "")
        match = _STATE_JSON_RE.search(body)
        if match:
            json_str = match.group(1).strip()
            try:
                data = json.loads(json_str)
                return WorkflowState.model_validate(data)
            except (json.JSONDecodeError, Exception) as e:
                print(f"Warning: Failed to parse workflow state: {e}", flush=True)
                return None
    return None


def extract_state_from_body(body: str) -> Optional[WorkflowState]:
    """Extract WorkflowState from a single comment body string."""
    if not body:
        return None
    match = _STATE_JSON_RE.search(body)
    if match:
        json_str = match.group(1).strip()
        try:
            data = json.loads(json_str)
            return WorkflowState.model_validate(data)
        except (json.JSONDecodeError, Exception):
            return None
    return None


# ── State Building ─────────────────────────────────────────────────────


def build_state(
    prev_state: Optional[WorkflowState],
    *,
    workflow: str = "",
    step: str = "",
    agent: str = "",
    phase: str = "",
    summary: str = "",
    comment_id: Optional[int] = None,
    artifacts: Optional[list[str]] = None,
    pin: bool = False,
    metrics_dict: Optional[dict[str, Any]] = None,
    context_updates: Optional[dict[str, Any]] = None,
    parent_workflow: Optional[str] = None,
    parent_workflow_step: Optional[str] = None,
) -> WorkflowState:
    """
    Build a new WorkflowState by advancing from a previous state.

    This is a pure state-transition function: given a previous state (or None
    for initial state) and the current step's data, returns a new WorkflowState
    with the history advanced and all fields updated.

    Args:
        prev_state: Previous state, or None for initial state.
        workflow: Current workflow name.
        step: Current step name.
        agent: Current agent role.
        phase: Current phase name.
        summary: 1-2 sentence summary of this step's output.
        comment_id: GitHub comment ID for this step's output.
        artifacts: List of artifact names produced by this step.
        pin: If True, add this step's comment_id to pinned_comment_ids.
        metrics_dict: Full metrics dict from the current agent run.
        context_updates: Key-value pairs to merge into current_context.
        parent_workflow: Parent workflow name (if in a sub-workflow).
        parent_workflow_step: Parent step name (if in a sub-workflow).

    Returns:
        A new WorkflowState instance.
    """
    if prev_state is None:
        base_version = 0
        base_history: list[HistoryEntry] = []
        base_pinned: list[int] = []
        base_context: dict[str, Any] = {}
        base_workflow = ""
        base_parent_wf = None
        base_parent_step = None
    else:
        base_version = prev_state.version
        base_history = list(prev_state.history)
        base_pinned = list(prev_state.pinned_comment_ids)
        base_context = dict(prev_state.current_context)
        base_workflow = prev_state.workflow
        base_parent_wf = prev_state.parent_workflow
        base_parent_step = prev_state.parent_workflow_step

    # Build history entry for the current step
    entry = HistoryEntry(
        step=step,
        agent=agent,
        summary=summary,
        comment_id=comment_id,
        artifacts=artifacts or [],
        pin=pin,
    )
    new_history = base_history + [entry]

    # Handle pinned comments
    new_pinned = list(base_pinned)
    if pin and comment_id:
        if comment_id not in new_pinned:
            new_pinned.append(comment_id)

    # Build metrics
    new_metrics = None
    if metrics_dict:
        new_metrics = StateMetrics.from_full_metrics_dict(metrics_dict)

    # Merge context updates
    new_context = dict(base_context)
    if context_updates:
        new_context.update(context_updates)

    # Determine parent workflow tracking
    new_parent_wf = parent_workflow if parent_workflow is not None else base_parent_wf
    new_parent_step = parent_workflow_step if parent_workflow_step is not None else base_parent_step

    return WorkflowState(
        version=base_version + 1,
        workflow=workflow or base_workflow,
        step=step,
        agent=agent,
        phase=phase,
        history=new_history,
        pinned_comment_ids=new_pinned,
        metrics=new_metrics,
        current_context=new_context,
        parent_workflow=new_parent_wf,
        parent_workflow_step=new_parent_step,
    )


# ── Formatting ─────────────────────────────────────────────────────────


def state_to_json(state: WorkflowState, compact: bool = False) -> str:
    """Serialize WorkflowState to a JSON string."""
    dump = state.model_dump(mode="json", exclude_defaults=True)
    if compact:
        return json.dumps(dump, separators=(",", ":"))
    return json.dumps(dump, indent=2)


def format_collapsed_section(state: WorkflowState) -> str:
    """
    Format the WorkflowState as a GitHub-flavored Markdown collapsed section.

    Output:
    <details>
    <summary>🤖 Workflow State (v{version})</summary>

    ```json
    { ... }
    ```

    </details>

    The state markers (WORKFLOW_STATE_START/END) are embedded inside the
    code block for reliable extraction by scripts.

    Args:
        state: The WorkflowState to format.

    Returns:
        Markdown string ready to prepend to a comment body.
    """
    json_str = state_to_json(state, compact=False)
    summary_label = f"🤖 Workflow State (v{state.version})"

    lines = [
        "<details>",
        f"<summary>{summary_label}</summary>",
        "",
        "```json",
        STATE_MARKER_START,
        json_str,
        STATE_MARKER_END,
        "```",
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines)


def wrap_comment_with_state(state: WorkflowState, body: str) -> str:
    """
    Prepend the collapsed state section to a comment body.

    Args:
        state: The WorkflowState to embed.
        body: The original comment body (human-readable content).

    Returns:
        Complete comment string with state section at the top.
    """
    section = format_collapsed_section(state)
    return section + body


def strip_state_section(body: str) -> str:
    """Remove the state collapsed section from a comment body, returning just the human-readable content."""
    # Remove the entire <details>...</details> block
    cleaned = re.sub(r"<details>.*?</details>\s*", "", body, flags=re.DOTALL)
    return cleaned.strip()


# ── Compatibility Helpers ──────────────────────────────────────────────


def legacy_metrics_to_state_metrics(metrics_json_str: str) -> Optional[StateMetrics]:
    """Convert a legacy <!-- agent-metrics: {...} --> JSON string to StateMetrics."""
    try:
        data = json.loads(metrics_json_str)
        return StateMetrics.from_full_metrics_dict(data)
    except (json.JSONDecodeError, Exception):
        return None


LEGACY_METRICS_RE = re.compile(r"<!--\s*agent-metrics:\s*(.*?)\s*-->")


def extract_legacy_metrics(body: str) -> dict[str, Any]:
    """Extract metrics JSON from a legacy HTML comment marker."""
    if not body:
        return {}
    match = LEGACY_METRICS_RE.search(body)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
    return {}
