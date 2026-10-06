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


def state_to_json(state: WorkflowState, compact: bool = False) -> str:
    """Serialize WorkflowState to a JSON string."""
    dump = state.model_dump(mode="json", exclude_defaults=True)
    cleaned = _collapse_empty_dicts(dump)
    if compact:
        return json.dumps(cleaned, separators=(",", ":"))
    return json.dumps(cleaned, indent=2)