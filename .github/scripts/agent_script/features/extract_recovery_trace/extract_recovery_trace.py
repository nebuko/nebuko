"""Recovery trace extraction — pure function for building execution summaries from conversation events.

When an agent fails to produce a final message, this module extracts a concise
execution trace from the conversation's event history. The trace is stored in
the workflow state's current_context so a restarted agent can resume where
the previous one left off.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from openhands.sdk import get_logger
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import ObservationEvent

logger = get_logger(__name__)


@dataclass(frozen=True)
class ToolCallTrace:
    """Immutable record of a single tool invocation."""

    tool_name: str
    input_summary: str = ""
    output_summary: str = ""
    error: bool = False


@dataclass(frozen=True)
class RecoveryTrace:
    """Immutable summary of an agent's partial execution."""

    agent_role: str
    total_tool_calls: int
    errors_count: int
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[ToolCallTrace] = field(default_factory=list)


def _truncate(text: str, max_chars: int = 500) -> str:
    """Truncate text to max_chars, breaking at paragraph boundary if possible."""
    if len(text) <= max_chars:
        return text.strip()
    truncated = text[:max_chars]
    last_break = truncated.rfind("\n\n")
    if last_break > max_chars // 3:
        truncated = truncated[:last_break]
    return truncated.rstrip() + "..."


def _summarize_action_input(event: ActionEvent) -> str:
    """Extract a short summary of an action event's input parameters."""
    props = event.__dict__ if hasattr(event, "__dict__") else {}

    # Terminal commands
    if event.tool_name == "terminal" and "command" in props:
        cmd = props["command"]
        return f"run: {cmd}" if isinstance(cmd, str) else str(cmd)[:200]

    # File editor operations
    if event.tool_name == "file_editor" and "command" in props:
        cmd = props.get("command", "")
        path = props.get("path", "")
        return f"{cmd} {path}"

    # Task tracker
    if event.tool_name == "task_tracker":
        cmd = props.get("command", "")
        return f"task_tracker: {cmd}"

    # Think / Finish tools (no external tool_name usually)
    if not event.tool_name or event.tool_name == "unknown":
        msg = props.get("message", props.get("text", ""))
        if msg:
            return _truncate(str(msg), 200)

    # Generic fallback: serialize key properties
    parts = []
    for key in ("command", "summary", "message", "content", "path", "tool_name"):
        if key in props and props[key]:
            val = str(props[key])
            if key != "tool_name":
                val = _truncate(val, 200)
            parts.append(f"{key}={val}")
    return " ".join(parts[:3]) if parts else "(no input summary)"


def _summarize_observation_output(event: ObservationEvent) -> str:
    """Extract a short summary of an observation event's output."""
    if not hasattr(event, "observation") or not event.observation:
        return ""
    obs = event.observation

    content = getattr(obs, "content", "")
    if not content and hasattr(obs, "result"):
        content = getattr(obs, "result", "")

    if not isinstance(content, str):
        content = str(content)

    return _truncate(content, 400) if content else ""


def extract_recovery_trace(conversation, max_traces: int = 20) -> Optional[RecoveryTrace]:
    """Extract a recovery trace from a conversation's event history.

    Scans ActionEvent/ObservationEvent pairs to build a chronological
    summary of tool calls made by the agent. Returns None if there are
    no meaningful events.

    Args:
        conversation: The OpenHands Conversation object.
        max_traces: Maximum number of tool call traces to include.

    Returns:
        RecoveryTrace instance or None if no events found.
    """
    if not conversation or not hasattr(conversation, "state") or not conversation.state.events:
        return None

    events = conversation.state.events
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    agent_actions = [e for e in action_events if e.source == "agent"]

    if not agent_actions:
        return None

    # Build observation lookup: action_id -> ObservationEvent
    obs_map: dict[int, ObservationEvent] = {}
    for obs_event in events:
        if isinstance(obs_event, ObservationEvent):
            if hasattr(obs_event, "id"):
                obs_map[obs_event.id] = obs_event

    # Match actions to observations via action_id reference
    traces: list[ToolCallTrace] = []
    errors_count = 0

    for action in agent_actions[:max_traces]:
        tool_name = getattr(action, "tool_name", "unknown") or "unknown"
        input_summary = _summarize_action_input(action)

        # Find matching observation
        output_summary = ""
        is_error = False

        # Try to find the observation linked to this action
        if hasattr(action, "id") and action.id in obs_map:
            obs = obs_map[action.id]
            output_summary = _summarize_observation_output(obs)
            if (hasattr(obs, "observation")
                    and hasattr(obs.observation, "is_error")
                    and obs.observation.is_error):
                is_error = True
                errors_count += 1

        traces.append(
            ToolCallTrace(
                tool_name=tool_name,
                input_summary=input_summary,
                output_summary=output_summary,
                error=is_error,
            )
        )

    # Extract token usage if available
    input_tokens = 0
    output_tokens = 0
    try:
        combined_metrics = conversation.conversation_stats.get_combined_metrics()
        if combined_metrics.accumulated_token_usage:
            input_tokens = combined_metrics.accumulated_token_usage.prompt_tokens
            output_tokens = combined_metrics.accumulated_token_usage.completion_tokens
    except Exception:
        pass

    role_name = os.environ.get("ROLE_NAME", "unknown")

    return RecoveryTrace(
        agent_role=role_name,
        total_tool_calls=len(traces),
        errors_count=errors_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=traces,
    )


def recovery_trace_to_markdown(trace: RecoveryTrace, max_length: int = 8000) -> str:
    """Format a RecoveryTrace as a markdown section for injection into prompts.

    Produces a compact but readable summary showing what the previous agent
    attempted before it failed.

    Args:
        trace: The RecoveryTrace to format.
        max_length: Maximum character length of the output.

    Returns:
        Markdown-formatted string.
    """
    if not trace:
        return ""

    lines = [
        "## Previous Execution Log",
        "",
        f"- **Agent:** {trace.agent_role}",
        f"- **Tool Calls:** {trace.total_tool_calls}",
        f"- **Errors:** {trace.errors_count}",
        f"- **Input Tokens:** {trace.input_tokens}",
        f"- **Output Tokens:** {trace.output_tokens}",
        "",
        "### Execution Trace",
        "",
    ]

    for i, call in enumerate(trace.tool_calls, start=1):
        error_marker = " ❌" if call.error else ""
        lines.append(f"**{i}. [{call.tool_name}]{error_marker}**")
        if call.input_summary and call.input_summary != "(no input summary)":
            lines.append(f"   - Input: `{call.input_summary}`")
        if call.output_summary:
            lines.append(f"   - Output: `{call.output_summary}`")
        lines.append("")

    result = "\n".join(lines)

    # Truncate if needed
    if len(result) > max_length:
        result = result[:max_length] + "\n\n*(execution trace truncated)*"

    return result
