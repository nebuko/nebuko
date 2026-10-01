#!/usr/bin/env python3
"""
Normalize GitHub event data for the multi-agent workflow.

Reads the GitHub event payload and extracts TARGET_NUMBER, TRIGGER_BODY,
TRIGGER_AUTHOR, and EVENT_TYPE into environment variable format.

Usage:
    python event_normalizer.py --event-path "$GITHUB_EVENT_PATH" \\
        [--event-name <name>] [--issue-number <n>] [--prompt-body <text>]

Output:
    KEY=value pairs to stdout (for eval $(...) consumption in workflows).
    Multi-line values use HEREDOC format (KEY<<EOF ... EOF).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only (issue #590)
    from engine.models import Trigger


def normalize_trigger(payload: Mapping[str, Any]) -> Trigger:
    """Pure payload -> :class:`Trigger` extraction for the engine seam.

    ``normalize_event`` (and the CLI ``main()``) stay the GHA-facing wrappers;
    this function carries the extraction semantics without touching the event
    file or the environment.

    Args:
        payload: Already-parsed GitHub event payload.

    Returns:
        The normalized Trigger.
    """
    from engine.models import Trigger

    comment = payload.get("comment")
    if comment is not None:
        return Trigger(
            author=comment.get("user", {}).get("login", ""),
            body=comment.get("body", "") or "",
            type="issue_comment",
            number=payload.get("issue", {}).get("number") or 0,
            payload=payload,
        )

    issue = payload.get("issue") or {}
    return Trigger(
        author=issue.get("user", {}).get("login", ""),
        body=issue.get("body", "") or "",
        type="issue",
        number=issue.get("number") or 0,
        payload=payload,
    )


def normalize_event(
    event_path: str,
    event_name: str,
    issue_number: int = 0,
    prompt_body: str = "",
    triggering_actor: str = "",
    actor: str = "",
) -> dict:
    """Parse GitHub event payload and extract normalized fields.

    Args:
        event_path: Path to the GitHub event JSON file.
        event_name: GitHub event name (issue_comment, issues, workflow_call).
        issue_number: Issue number (for issue_comment/issues events).
        prompt_body: Body text for workflow_call events.
        triggering_actor: Actor who triggered a workflow_call.
        actor: Fallback actor name.

    Returns:
        Dict with TARGET_NUMBER, TRIGGER_BODY, TRIGGER_AUTHOR, EVENT_TYPE.
    """
    with open(event_path, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    if event_name == "issue_comment":
        trigger_body = event_data.get("comment", {}).get("body", "")
        trigger_author = event_data.get("comment", {}).get("user", {}).get("login", "")
        target_number = issue_number
        event_type = "issue_comment"
    elif event_name == "issues":
        trigger_body = event_data.get("issue", {}).get("body", "")
        trigger_author = event_data.get("issue", {}).get("user", {}).get("login", "")
        target_number = issue_number
        event_type = "issues"
    elif event_name == "workflow_call":
        trigger_body = prompt_body
        trigger_author = triggering_actor or actor
        target_number = 0
        event_type = "workflow_call"
    else:
        print(f"Unknown event type: {event_name}", file=sys.stderr)
        sys.exit(1)

    return {
        "TARGET_NUMBER": str(target_number),
        "TRIGGER_BODY": trigger_body or "",
        "TRIGGER_AUTHOR": trigger_author or "",
        "EVENT_TYPE": event_type,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Normalize GitHub event data for workflow consumption"
    )
    parser.add_argument(
        "--event-path",
        default=os.environ.get("GITHUB_EVENT_PATH", ""),
        help="Path to GitHub event JSON file",
    )
    parser.add_argument(
        "--event-name",
        default=os.environ.get("EVENT_NAME", os.environ.get("GITHUB_EVENT_NAME", "")),
        help="GitHub event name",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=0,
        help="Issue number (for issue_comment/issues)",
    )
    parser.add_argument(
        "--prompt-body",
        default="",
        help="Prompt body text (for workflow_call)",
    )
    parser.add_argument(
        "--triggering-actor",
        default="",
        help="Actor who triggered the workflow",
    )
    parser.add_argument(
        "--actor",
        default="",
        help="Fallback actor name",
    )
    parser.add_argument(
        "--output-format",
        choices=["env", "json"],
        default="env",
        help="Output format: env (KEY=value) or json",
    )

    args = parser.parse_args()

    if not args.event_path:
        print("Error: --event-path or GITHUB_EVENT_PATH is required", file=sys.stderr)
        sys.exit(1)

    if not args.event_name:
        print("Error: --event-name or GITHUB_EVENT_NAME is required", file=sys.stderr)
        sys.exit(1)

    result = normalize_event(
        event_path=args.event_path,
        event_name=args.event_name,
        issue_number=args.issue_number,
        prompt_body=args.prompt_body,
        triggering_actor=args.triggering_actor,
        actor=args.actor,
    )

    if args.output_format == "json":
        print(json.dumps(result))
    else:
        # Output as env vars for workflow consumption
        for key, value in result.items():
            if "\n" in value:
                print(f"{key}<<EOF")
                print(value)
                print("EOF")
            else:
                print(f"{key}={value}")

    # Also write to GITHUB_ENV if available
    github_env = os.environ.get("GITHUB_ENV", "")
    if github_env:
        with open(github_env, "a") as f:
            for key, value in result.items():
                if key == "TRIGGER_BODY":
                    # Use heredoc for multi-line
                    f.write(f"{key}<<EOF\n{value}\nEOF\n")
                else:
                    f.write(f"{key}={value}\n")

    print(f"Normalized event: {result['EVENT_TYPE']} -> TARGET_NUMBER={result['TARGET_NUMBER']}", file=sys.stderr)


if __name__ == "__main__":
    main()
