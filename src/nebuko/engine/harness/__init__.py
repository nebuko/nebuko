"""Harness port — one seam for every agent runtime (issue #1368).

Import the port surface from here; the shipped adapters are registered lazily
by :func:`resolve_harness` so importing the package never pulls a runtime SDK.
"""

from .base import (
    BuiltAgent,
    ClosingChannel,
    ClosingResult,
    HarnessAdapter,
    HarnessCapabilities,
    HarnessEvent,
    HarnessSession,
    drive_turn,
    register_harness,
    registered_harnesses,
    resolve_harness,
)

__all__ = [
    "BuiltAgent",
    "ClosingChannel",
    "ClosingResult",
    "HarnessAdapter",
    "HarnessCapabilities",
    "HarnessEvent",
    "HarnessSession",
    "drive_turn",
    "register_harness",
    "registered_harnesses",
    "resolve_harness",
]
