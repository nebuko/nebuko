#!/usr/bin/env python3
"""
Trigger parser module.

Extracts structured hints from a raw trigger message body: workflow/step/agent
overrides, commands (force-next-step), complexity indicators, and bot detection.

Pure functions only — no side effects, no I/O.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from pipeline_context import TriggerHints

# Valid agent names recognized by the system
_VALID_AGENTS = r"(?:issue-reviewer|genius|ctx|dev|review|issue)"


def parse_trigger(trigger_body: str) -> TriggerHints:
    """Parse a trigger message body and extract all available hints.

    Examines the trigger text in order:
    1. Bot detection (<!-- bot-agent- marker)
    2. /workflow commands (workflow name override, force-next-step)
    3. @@mention agent names
    4. <agentRunConfig> XML blocks (backward compatible)
    5. Trailing JSON objects with "task" field

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        TriggerHints dataclass with all parsed fields.
    """
    if not trigger_body:
        return TriggerHints()

    # Bot detection
    is_bot = _is_bot_comment(trigger_body)

    # /workflow command (first line only)
    workflow_name = _extract_workflow_command(trigger_body)
    force_next = _extract_force_next_command(trigger_body)

    # @@mention agent name
    agent_name = _extract_mention_agent(trigger_body)

    # Structured config: <agentRunConfig> XML block or trailing JSON
    config = _extract_agent_run_config(trigger_body)

    hints = TriggerHints(
        workflow_name=workflow_name,
        force_next_step=force_next,
        agent_name=agent_name,
        is_bot_comment=is_bot,
    )

    if config:
        hints.task_hint = config.get("task", "") or hints.task_hint
        hints.complexity = config.get("complexity", "medium") or hints.complexity
        hints.reasoning = config.get("reasoning", "none") or hints.reasoning
        hints.model = config.get("model") or hints.model
        hints.temperature = config.get("temperature") or hints.temperature
        hints.skills = config.get("skills") or hints.skills
        if config.get("workflow"):
            hints.workflow_name = config["workflow"] or hints.workflow_name
        if config.get("step"):
            hints.step_name = config["step"] or hints.step_name

    return hints


def _is_bot_comment(trigger_body: str) -> bool:
    """Check if first 5 lines contain a bot-agent marker."""
    first_lines = "\n".join(trigger_body.split("\n")[:5])
    return "<!-- bot-agent-" in first_lines


def _extract_workflow_command(trigger_body: str) -> Optional[str]:
    """Extract /workflow <name> from the first line."""
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+(\S+)", first_line)
    return match.group(1) if match else None


def _extract_force_next_command(trigger_body: str) -> Optional[str]:
    """Extract '/workflow force-next-step' command from the first line.

    Returns the optional target step name, or 'force-next-step' string
    if no explicit target was provided.
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+force-next-step(?:\s+(\S+))?$", first_line)
    if match:
        return match.group(1) if match.group(1) else "force-next-step"
    return None


def _extract_mention_agent(trigger_body: str) -> Optional[str]:
    """Extract the last @@mention agent name from trigger body."""
    agents = re.findall(r"@@(" + _VALID_AGENTS + r")", trigger_body)
    return agents[-1] if agents else None


def _extract_agent_run_config(trigger_body: str) -> Optional[dict]:
    """Extract agentRunConfig JSON from trigger body.

    Strategy 1: <agentRunConfig> JSON block (primary, backward compatible).
    Strategy 2: Trailing JSON object with valid "task" field.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Parsed dict or None.
    """
    # Strategy 1: <agentRunConfig> JSON block
    match = re.search(r"<agentRunConfig>(.*?)</agentRunConfig>", trigger_body, re.DOTALL)
    if match:
        json_str = match.group(1).strip().replace("\n", "")
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Strategy 2: Trailing JSON object with valid task field
    match = re.search(
        r"\{[^{}]*\"task\"\s*:\s*\"" + _VALID_AGENTS + r"\"[^{}]*\}",
        trigger_body,
    )
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None
