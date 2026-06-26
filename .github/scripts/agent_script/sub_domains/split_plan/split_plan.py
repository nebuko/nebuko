"""Split plan extraction and formatting — pure functions for <splitPlan> XML blocks."""

import json
import re


def extract_split_plan(message: str) -> str | None:
    """
    Extract <splitPlan>...</splitPlan> JSON block from a message.

    Scans the entire message for the XML-like tags and returns the
    JSON content between them, or None if no split plan is present.

    Args:
        message: The agent's final message (may contain <splitPlan> tags).

    Returns:
        Raw JSON string from inside the tags, or None.
    """
    if not message:
        return None

    match = re.search(r"<splitPlan>\s*(.*?)\s*</splitPlan>", message, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def strip_split_plan(message: str) -> str:
    """
    Remove <splitPlan>...</splitPlan> block from a message.

    Args:
        message: The agent's final message.

    Returns:
        Message with the <splitPlan> block removed (including surrounding blank lines).
    """
    return re.sub(r"\n*<splitPlan>.*?</splitPlan>\n*", "\n\n", message, flags=re.DOTALL).strip() + "\n"


def split_plan_summary(json_str: str) -> str:
    """
    Generate a human-readable markdown summary from a split plan JSON.

    Args:
        json_str: The split plan JSON string.

    Returns:
        A markdown table summarizing tasks and their dependencies.
    """
    try:
        plan = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return ""

    tasks = plan.get("tasks", [])
    if not tasks:
        return ""

    total = len(tasks)
    lines = [f"---\n📋 **Split Plan** — {total} sub-task{'s' if total != 1 else ''} will be created:", ""]
    lines.append("| # | Task | Dependencies |")
    lines.append("|---|------|-------------|")

    for i, task in enumerate(tasks, start=1):
        title = task.get("title", "")
        deps = task.get("dependencies", [])
        dep_str = ", ".join(f"#{d + 1}" for d in deps) if deps else "—"
        lines.append(f"| {i} | {title} | {dep_str} |")

    return "\n".join(lines)
