"""``nebuko init`` — prepare a repository (or organization) for Nebuko.

The package is split into ports (``ports``), adapters (``providers``,
``sources``, ``refs``, ``github_host``), the orchestration (``setup``) and the
command surface (``cli``). Everything external — the network, the ``gh`` CLI,
the terminal — sits behind a port so the flow is unit-testable.
"""

from __future__ import annotations

__all__ = ["cli", "github_host", "ports", "providers", "refs", "setup", "sources"]
