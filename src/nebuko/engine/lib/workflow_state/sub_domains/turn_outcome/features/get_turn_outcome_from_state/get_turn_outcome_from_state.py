from typing import Optional

from .....features.get_last_state.get_last_state import extract_state_from_body
from .....workflow_state import WorkflowState
from ...turn_outcome import TurnOutcome


def extract_turn_outcome_from_state(
    body: str, prev_state: Optional[WorkflowState] = None
) -> Optional[TurnOutcome]:
    """Extract the TurnOutcome from a comment body's embedded WorkflowState.

    If prev_state is provided and carries an outcome, return it directly.
    Otherwise, parse the body for a WorkflowState and extract its outcome.

    Args:
        body: Comment body text.
        prev_state: Previously extracted WorkflowState (optional cache).

    Returns:
        TurnOutcome instance or None.
    """
    if prev_state and prev_state.turn_outcome:
        return prev_state.turn_outcome

    state = extract_state_from_body(body)
    if state and state.turn_outcome:
        return state.turn_outcome
    return None
