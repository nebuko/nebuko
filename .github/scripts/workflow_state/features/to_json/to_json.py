from ...workflow_state import WorkflowState

import json

def state_to_json(state: WorkflowState, compact: bool = False) -> str:
    """Serialize WorkflowState to a JSON string."""
    dump = state.model_dump(mode="json", exclude_defaults=True)
    if compact:
        return json.dumps(dump, separators=(",", ":"))
    return json.dumps(dump, indent=2)