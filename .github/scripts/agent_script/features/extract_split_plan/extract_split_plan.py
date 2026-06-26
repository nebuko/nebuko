"""Extract split plan feature — orchestrates split plan extraction and stripping."""

from agent_script.sub_domains.split_plan.split_plan import (
    extract_split_plan as _extract_split_plan,
    strip_split_plan as _strip_split_plan,
    split_plan_summary,
)


def extract_and_strip_split_plan(message: str) -> tuple[str | None, str]:
    """
    Extract <splitPlan> JSON from a message and return the stripped message.

    Args:
        message: The agent's final message (may contain <splitPlan> tags).

    Returns:
        Tuple of (json_str or None, stripped_message).
    """
    json_str = _extract_split_plan(message)
    stripped = _strip_split_plan(message)
    return (json_str, stripped)


# Alias for facade compatibility
generate_split_plan_summary = split_plan_summary

# Re-export from sub-domain for direct usage
__all__ = ["extract_and_strip_split_plan", "split_plan_summary", "generate_split_plan_summary"]
