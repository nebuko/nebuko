from typing import Any, Dict, Optional

from ...function_call import FunctionCall


def extract_function_call(data: Optional[Dict[str, Any]]) -> Optional[FunctionCall]:
    """Extract a FunctionCall from an AGENT_RUN_CONFIG payload dict.

    The payload carries dispatch and function_call as sibling sections:
        {"dispatch": {...}, "function_call": {"function": "call_agent", ...}}
    Invalid payloads return None (invalid function calls must not corrupt
    the dispatch path).

    Args:
        data: Parsed AGENT_RUN_CONFIG JSON payload.

    Returns:
        A validated FunctionCall, or None when absent/invalid.
    """
    if not isinstance(data, dict):
        return None
    raw = data.get("function_call")
    if not isinstance(raw, dict):
        return None
    try:
        return FunctionCall.model_validate(raw)
    except Exception:
        return None


__all__ = ["extract_function_call"]
