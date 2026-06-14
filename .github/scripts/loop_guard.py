#!/usr/bin/env python3
"""
Prevent agent-trigger loops and detect human ping patterns.

Migrates 'Prevent all agent-trigger loops & human ping guard' bash block.

Two guards:
1. Same-agent loop detection: if the last bot comment matches the current
   agent being invoked, skip (prevent infinite loop).
   Cross-agent calls are allowed (e.g., genius -> ctx).
2. Human+agent ping detection: if a human is pinged alongside an agent,
   skip to let the human orchestrate.

Usage:
    python loop_guard.py --current-agent <name> --trigger-body <file|->

Output:
    SKIP_AGENT=true or SKIP_AGENT=false to stdout.
"""

import argparse
import os
import re
import sys


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


def detect_human_plus_agent_ping(trigger_body: str) -> bool:
    """Detect if both a human and an agent are pinged in the same message.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if human + agent ping pattern detected.
    """
    agent_mentions = re.findall(
        r"@@(?:issue-reviewer|genius|ctx|dev|review|issue)",
        trigger_body,
    )

    # Find single @mentions (not @@) — these are human pings
    # Use negative lookbehind to skip @@ patterns
    human_mentions = re.findall(r"(?<!@)@[a-zA-Z0-9_-]+", trigger_body)

    return len(agent_mentions) > 0 and len(human_mentions) > 0


def check_loop_guard(trigger_body: str, current_agent: str) -> dict:
    """Run both loop guards and determine if agent should skip.

    Args:
        trigger_body: Raw trigger body text.
        current_agent: Current agent name being invoked.

    Returns:
        Dict with SKIP_AGENT (true/false) and optional REASON.
    """
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
