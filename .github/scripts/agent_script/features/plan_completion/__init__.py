"""Plan-step completion marker (issue #613, G3)."""
from .plan_completion import (
    completion_marker_body,
    fetch_existing_bodies,
    marker_already_present,
    post_completion_marker,
    post_terminal_plan_completion,
    resolve_completion_index,
)

__all__ = [
    "completion_marker_body",
    "fetch_existing_bodies",
    "marker_already_present",
    "post_completion_marker",
    "post_terminal_plan_completion",
    "resolve_completion_index",
]