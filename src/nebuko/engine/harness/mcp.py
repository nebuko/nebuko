"""Shared MCP plumbing for the harnesses (issue #1368).

The declarative ``mcp_servers`` catalog is a *harness-independent* spec: each
entry's ``env`` maps a child-process variable to the *name* of the OS env var
holding its value. Each adapter turns the resolved specs into its own server
handles, so the resolution logic lives here once.
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = ["build_mcp_servers", "resolve_mcp_env"]

log = logging.getLogger(__name__)


def resolve_mcp_env(env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Resolve each child env var to the value of the OS env var it names.

    Returns the resolved child environment and the names of any OS env vars
    that are unset or empty (so the caller can skip that server).
    """
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for child_var, source_name in env.items():
        value = os.environ.get(source_name)
        if value:
            resolved[child_var] = value
        else:
            missing.append(source_name)
    return resolved, missing


def build_mcp_servers(agent_cfg: Any) -> dict[str, Any]:
    """Turn declarative ``mcp_servers`` into OpenHands SDK server handles.

    An unset env reference skips that server with a warning so a missing secret
    degrades one capability instead of failing the whole run.
    """
    if agent_cfg is None or not getattr(agent_cfg, "mcp_servers", None):
        return {}

    from openhands.sdk.mcp import MCPServer  # noqa: PLC0415 - optional runtime

    servers: dict[str, Any] = {}
    for spec in agent_cfg.mcp_servers:
        resolved_env, missing = resolve_mcp_env(spec.env)
        if missing:
            log.warning(
                f"MCP server '{spec.name}' skipped — unset env var(s): "
                f"{', '.join(missing)}"
            )
            continue
        server = MCPServer(
            command=spec.command,
            args=list(spec.args),
            env=dict(resolved_env),
            transport=spec.transport,
        )
        # Values are already resolved from the OS env; mark them literal so the
        # SDK's ${VAR} expansion pass cannot re-interpret a resolved secret.
        servers[spec.name] = server.as_literal()
    return servers
