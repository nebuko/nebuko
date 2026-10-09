from ...workflow_state import WorkflowState

import json


def _collapse_empty_dicts(data):
    """Recursively remove empty dicts from a serialized structure.

    When model_dump(exclude_defaults=True) is called on a nested Pydantic
    model whose fields are all defaults, the nested object serializes as {}.
    This function collapses those empty dicts so they vanish entirely,
    making all-defaults nested models visually equivalent to None.
    """
    if isinstance(data, dict):
        cleaned = {}
        for key, value in data.items():
            collapsed = _collapse_empty_dicts(value)
            if collapsed is not None and collapsed != {}:
                cleaned[key] = collapsed
        return cleaned
    if isinstance(data, list):
        cleaned = []
        for item in data:
            collapsed = _collapse_empty_dicts(item)
            if collapsed is not None:
                cleaned.append(collapsed)
        return cleaned
    return data


def _is_scalar(value) -> bool:
    """True for JSON leaves: scalars, not objects or arrays."""
    return not isinstance(value, (dict, list))


def _is_scalar_array(value: list) -> bool:
    """True when every element is a scalar, so the array fits on one line."""
    return all(_is_scalar(item) for item in value)


def _format_json(value, indent: int, level: int) -> str:
    """Pretty-print a JSON value, keeping arrays of scalars on a single line.

    ``json.dumps(indent=...)`` puts every array element on its own line, so a
    long numeric array such as ``agent_runtimes_per_call_ms`` dominates the
    comment-embedded workflow state (issue #845). Arrays whose elements are all
    scalars are rendered inline; arrays of objects keep the standard
    multi-line shape, and objects are always expanded.
    """
    pad = " " * (indent * level)
    child_pad = " " * (indent * (level + 1))

    if isinstance(value, dict):
        if not value:
            return "{}"
        body = ",\n".join(
            f"{child_pad}{json.dumps(key)}: {_format_json(item, indent, level + 1)}"
            for key, item in value.items()
        )
        return "{\n" + body + "\n" + pad + "}"

    if isinstance(value, list):
        if not value:
            return "[]"
        if _is_scalar_array(value):
            return "[" + ", ".join(json.dumps(item) for item in value) + "]"
        body = ",\n".join(
            f"{child_pad}{_format_json(item, indent, level + 1)}" for item in value
        )
        return "[\n" + body + "\n" + pad + "]"

    return json.dumps(value)


def state_to_json(state: WorkflowState, compact: bool = False) -> str:
    """Serialize WorkflowState to a JSON string."""
    dump = state.model_dump(mode="json", exclude_defaults=True)
    cleaned = _collapse_empty_dicts(dump)
    if compact:
        return json.dumps(cleaned, separators=(",", ":"))
    return _format_json(cleaned, indent=2, level=0)