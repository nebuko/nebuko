"""Platform adapter factory (issue #590, Stage A).

The single bootstrap location that wires a concrete adapter to the
``PlatformAdapter`` port, keeping adapter selection out of the engine.

Stub step: skeleton only. Bodies are materialised by @@dev in the implement
step.
"""

from __future__ import annotations

from typing import Any

from adapters.base import PlatformAdapter

__all__ = ["PlatformAdapter", "build_adapter"]


# @stub-protected
def build_adapter(kind: str, **kwargs: Any) -> PlatformAdapter:
    """Select the adapter for ``kind`` (``"github"`` | ``"local"``) and forward ``kwargs``."""
    raise NotImplementedError
