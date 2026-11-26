"""Dispatch-actor sub-domain package (issue #1482)."""

from .dispatch_actor import (
    DispatchActorCheck,
    lookup_collaborator_association,
    personal_dispatch_actor,
    verify_dispatch_actor,
)

__all__ = [
    "DispatchActorCheck",
    "lookup_collaborator_association",
    "personal_dispatch_actor",
    "verify_dispatch_actor",
]
