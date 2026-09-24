"""Trigger commands value object.

Bundles the fields extracted from the raw trigger body into a single
typed value object consumed by the routing cascade in the facade.
"""

from dataclasses import dataclass
from typing import Optional

from ..workflow_command.workflow_command import WorkflowCommand

__all__ = ["TriggerCommands"]


@dataclass(frozen=True)
class TriggerCommands:
    """Immutable bundle of command flags parsed from a trigger body.

    Attributes:
        is_bot: True if the trigger body looks like a bot comment.
        mention_agent: Last @@mention agent name, or None.
        workflow_command: Parsed /workflow <action> <resource> --flag value
            command, or None when the first line does not parse as one.
        debug: True if DEBUG=true was found in the body.
    """

    is_bot: bool
    mention_agent: Optional[str]
    workflow_command: Optional[WorkflowCommand]
    debug: bool