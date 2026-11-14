from typing import Any, Dict, Optional

from ...function_call import FunctionCall


def extract_function_call(data: Optional[Dict[str, Any]]) -> Optional[FunctionCall]:
    """Extract a FunctionCall from a harvested tool-call payload dict.

    The payload carries the turn outcome and the function_call as sibling
    sections:
        {"turn_outcome": {...}, "function_call": {"function": "call_agent", ...}}
    Invalid payloads return None (an invalid function call must not corrupt
    the routing path).

    Args:
        data: Harvested tool-call payload dict.

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
