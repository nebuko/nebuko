"""Trigger commands value object.

Bundles the fields extracted from the raw trigger body into a single
typed value object consumed by the routing cascade in the facade.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

__all__ = ["TriggerCommands"]


@dataclass(frozen=True)
class TriggerCommands:
    """Immutable bundle of command flags parsed from a trigger body.

    Attributes:
        is_bot: True if the trigger body looks like a bot comment.
        mention_agent: Last @@mention agent name, or None.
        workflow_cmd: /workflow <name> command value, or None.
        force_next: ('force-next-step', target_step_or_none) tuple, or None.
        resume: /workflow resume input text ("" for bare resume), or None.
        debug: True if DEBUG=true was found in the body.
    """

    is_bot: bool
    mention_agent: Optional[str]
    workflow_cmd: Optional[str]
    force_next: Optional[Tuple[str, Optional[str]]]
    resume: Optional[str]
    debug: bool