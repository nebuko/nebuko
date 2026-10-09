#!/usr/bin/env python3
"""
Prevent agent-trigger loops and detect human ping patterns.

Migrates 'Prevent all agent-trigger loops & human ping guard' bash block.

Two guards:
1. Same-agent loop detection: if the last bot comment matches the current
   agent being invoked, skip (prevent infinite loop).
   Cross-agent calls are allowed (e.g., architect -> context).
2. Human+agent ping detection: if a human is pinged alongside an agent,
   skip to let the human orchestrate.

Usage:
    python loop_guard.py --current-agent <name> --trigger-body <file|->

Output:
    SKIP_AGENT=true or SKIP_AGENT=false to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import TYPE_CHECKING, Mapping, Optional, Sequence

from config import load_agent_names
from resolve_config.features.parse_trigger.parse_trigger import LEGACY_AGENT_NAMES

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only (issue #590)
    from nebuko.engine.models import CommentEntry

_WORKFLOW_STATE_START = "<!-- WORKFLOW_STATE_START -->"
_WORKFLOW_STATE_END = "<!-- WORKFLOW_STATE_END -->"


def _iter_workflow_states(trigger_body: str):
    """Yield each parsed WORKFLOW_STATE block in a trigger body (oldest last
    marker wins the comparison — the yml guard reads the last one too)."""
    pos = trigger_body.find(_WORKFLOW_STATE_START)
    while pos != -1:
        stop = trigger_body.find(_WORKFLOW_STATE_END, pos + len(_WORKFLOW_STATE_START))
        if stop == -1:
            break
        try:
            state = json.loads(trigger_body[pos + len(_WORKFLOW_STATE_START):stop].strip())
        except Exception:
            state = None
        if isinstance(state, dict):
            yield state
        pos = trigger_body.find(_WORKFLOW_STATE_START, stop + len(_WORKFLOW_STATE_END))


def trigger_step(trigger_body: str) -> str:
    """Flat ``step`` of the last parseable WORKFLOW_STATE block (v1 shape)."""
    step = ""
    for state in _iter_workflow_states(trigger_body):
        value = state.get("step")
        if isinstance(value, str) and value:
            step = value
    return step


def _flatten_position(position) -> str:
    """Flatten a posted position block to the outermost-first path form the
    resolver emits in ``WORKFLOW_POSITION_PATH`` (``wf/step|wf/step|...``).

    Accepts the v3 shape (``{"refs": [{"workflow", "step"}, ...]}`` or a
    bare refs list — issue #638) and legacy v2 nested blocks
    (``workflow_name``/``step_name`` with the inner node under ``step``).
    """
    parts: list[str] = []
    refs = position.get("refs") if isinstance(position, dict) else position
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict) and ref.get("workflow"):
                parts.append(f"{ref['workflow']}/{ref.get('step', '')}")
        return "|".join(parts)
    node = position
    while isinstance(node, dict) and node.get("workflow_name"):
        parts.append(f"{node['workflow_name']}/{node.get('step_name', '')}")
        node = node.get("step")
    return "|".join(parts)


def trigger_position_path(trigger_body: str) -> str:
    """Full mount-chain path posted by the trigger state (issue #618/#638).

    Reads the ``position`` block of the last parseable WORKFLOW_STATE —
    v3 flat ``refs`` list or legacy v2 nested dict; empty when the
    trigger is a legacy v1 state (no position) — callers then fall back
    to the flat step comparison.
    """
    path = ""
    for state in _iter_workflow_states(trigger_body):
        position = state.get("position")
        if isinstance(position, dict) and ("refs" in position or position.get("workflow_name")):
            path = _flatten_position(position)
        elif isinstance(position, list) and position:
            path = _flatten_position(position)
    return path


def check_same_agent_advance(
    trigger_body: str,
    resolved_position_path: str,
    resolved_step: str,
) -> dict:
    """Same-agent re-trigger decision, position-aware (issue #618).

    v2 (both sides carry a full mount-chain path): identical path is a
    true self loop (skip); a different path is a legitimate advance across
    or within sub-workflows (``step_1`` -> ``step_2`` re-enters the same
    leaf, so the flat step alone cannot tell them apart — allow).
    Legacy/ambiguous (either side has no position): fall back to the v1
    flat-step comparison — different step (or no trigger state) allows,
    same step skips.
    """
    trigger_path = trigger_position_path(trigger_body)
    if trigger_path and resolved_position_path:
        if trigger_path == resolved_position_path:
            return {
                "SKIP_AGENT": "true",
                "REASON": (
                    f"Same-agent loop detected (same v2 position "
                    f"'{trigger_path}'). Skipping."
                ),
            }
        return {
            "SKIP_AGENT": "false",
            "REASON": (
                f"Same-agent advance to new position "
                f"('{trigger_path}' -> '{resolved_position_path}'). Allowing."
            ),
        }
    trigger_step_name = trigger_step(trigger_body)
    if trigger_step_name and trigger_step_name != resolved_step:
        return {
            "SKIP_AGENT": "false",
            "REASON": (
                f"Same-agent advance to new step "
                f"('{trigger_step_name}' -> '{resolved_step}'). Allowing."
            ),
        }
    return {
        "SKIP_AGENT": "true",
        "REASON": (
            "Same-agent loop detected "
            f"(no position / same step '{trigger_step_name or resolved_step}'). Skipping."
        ),
    }


def detect_same_agent_loop(trigger_body: str, current_agent: str) -> bool:
    """Check if trigger body is from the same agent (loop detection).

    Args:
        trigger_body: Raw trigger body text.
        current_agent: Current agent name being invoked.

    Returns:
        True if same-agent loop detected.
    """
    match = re.search(r"<!-- bot-agent-([a-z][a-z-]*) -->", trigger_body)
    if match:
        caller_agent = match.group(1)
        return caller_agent == current_agent
    return False


_agent_mentions_re_cache: Optional["re.Pattern[str]"] = None


def _agent_mentions_re() -> "re.Pattern[str]":
    """Compiled ``@@agent`` pattern derived from the live agent roster.

    Deprecated names (issue #828) are included so this guard agrees with
    ``parse_trigger``: a ``@@genius`` ping is still an agent ping.
    """
    global _agent_mentions_re_cache
    if _agent_mentions_re_cache is None:
        names = sorted(
            set(load_agent_names()) | set(LEGACY_AGENT_NAMES),
            key=len,
            reverse=True,
        )
        alternatives = "|".join(re.escape(name) for name in names)
        _agent_mentions_re_cache = re.compile(rf"@@(?:{alternatives})(?![A-Za-z0-9_-])")
    return _agent_mentions_re_cache


def detect_human_plus_agent_ping(trigger_body: str) -> bool:
    """Detect if both a human and an agent are pinged in the same message.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if human + agent ping pattern detected.
    """
    agent_mentions = re.findall(_agent_mentions_re(), trigger_body)

    # Find single @mentions (not @@) — these are human pings
    # Use negative lookbehind to skip @@ patterns
    human_mentions = re.findall(r"(?<!@)@[a-zA-Z0-9_-]+", trigger_body)

    return len(agent_mentions) > 0 and len(human_mentions) > 0


def _entry_body(entry) -> str:
    """Read a comment body from either an API dict or a normalized history entry."""
    if isinstance(entry, Mapping):
        return entry.get("body", "") or ""
    return getattr(entry, "body", "") or ""


def check_loop_guard(
    trigger_body: str,
    current_agent: str,
    history: Sequence[CommentEntry] | None = None,
) -> dict:
    """Run both loop guards and determine if agent should skip.

    Args:
        trigger_body: Raw trigger body text.
        current_agent: Current agent name being invoked.
        history: Optional injected comment history (engine seam, issue #590);
            when ``None`` the current trigger-body behaviour is unchanged.

    Returns:
        Dict with SKIP_AGENT (true/false) and optional REASON.
    """
    # The trigger body is authoritative on the GHA path (the trigger *is* the
    # latest comment). Marker-less hosts may hand an empty body while the
    # thread still carries the last comment; only then does history stand in.
    if not (trigger_body or "").strip() and history:
        trigger_body = _entry_body(history[-1])

    # Guard 1: Same-agent loop detection
    if detect_same_agent_loop(trigger_body, current_agent):
        caller_match = re.search(r"<!-- bot-agent-([a-z][a-z-]*) -->", trigger_body)
        caller = caller_match.group(1) if caller_match else "?"
        return {
            "SKIP_AGENT": "true",
            "REASON": f"Same-agent loop detected ({caller} calling itself)",
        }

    if re.search(r"<!-- bot-agent-", trigger_body):
        # Bot marker found but different agent — cross-agent call is allowed
        return {"SKIP_AGENT": "false"}

    # Guard 2: Human+agent ping (only applies to non-bot messages)
    if detect_human_plus_agent_ping(trigger_body):
        return {
            "SKIP_AGENT": "true",
            "REASON": "Human ping + agent ping detected",
        }

    return {"SKIP_AGENT": "false"}


def main():
    parser = argparse.ArgumentParser(
        description="Prevent agent-trigger loops & human ping guard"
    )
    parser.add_argument(
        "--current-agent",
        required=True,
        help="Current agent name being invoked",
    )
    parser.add_argument(
        "--trigger-body",
        default="-",
        help="Path to trigger body file, or '-' for stdin",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Path to GITHUB_ENV file to append results",
    )

    args = parser.parse_args()

    if args.trigger_body == "-":
        trigger_body = sys.stdin.read()
    else:
        with open(args.trigger_body, "r", encoding="utf-8") as f:
            trigger_body = f.read()

    result = check_loop_guard(trigger_body, args.current_agent)

    for key, value in result.items():
        print(f"{key}={value}")

    if args.env_file:
        with open(args.env_file, "a") as f:
            for key, value in result.items():
                f.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
