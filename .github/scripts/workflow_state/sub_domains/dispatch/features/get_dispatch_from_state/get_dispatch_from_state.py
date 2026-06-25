from typing import Optional

from .....features.get_last_state.get_last_state import extract_state_from_body
from .....workflow_state import WorkflowState
from ...dispatch import Dispatch

def extract_dispatch_from_state(
    body: str, prev_state: Optional[WorkflowState] = None
) -> Optional[Dispatch]:
    """Extract Dispatch from a comment body's embedded WorkflowState.

    If prev_state is provided and contains a dispatch, return it directly.
    Otherwise, parse the body for a WorkflowState and extract its dispatch.

    Args:
        body: Comment body text.
        prev_state: Previously extracted WorkflowState (optional cache).

    Returns:
        Dispatch instance or None.
    """
    if prev_state and prev_state.dispatch:
        return prev_state.dispatch

    state = extract_state_from_body(body)
    if state and state.dispatch:
        return state.dispatch
    return None