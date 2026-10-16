"""Layered default paths for the shared ``engine/lib`` runtime (issue #741).

The typed engine resolves configuration through :class:`nebuko.engine.paths.NebukoPaths`.
The flat ``engine/lib`` modules cannot import that class: they run both in-process
(on a ``sys.path`` that holds ``lib`` bare) and inside the agent child process,
where the ``nebuko`` package itself is not importable. They therefore resolve the
same two layers from their own file location:

* ``<repo_root>/.nebuko/<kind>/`` — the repo's overrides (optional);
* ``<checkout>/defaults/<kind>/`` — the artifacts shipped with Nebuko.

Keep this in sync with :mod:`nebuko.engine.paths`; both are derived from the
package location, so no caller depends on the current working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["checkout_root", "config_dir", "config_path", "user_dir", "workflow_dirs"]


def checkout_root() -> Path:
    """Repo root of the checkout that holds ``engine/lib`` (i.e. ``parents[4]``)."""
    return Path(__file__).resolve().parents[4]


def user_dir() -> Path:
    """Per-repo override directory (``<cwd>/.nebuko``); may not exist."""
    return Path.cwd() / ".nebuko"


def config_dir(kind: str, env_var: str | None = None) -> str:
    """First existing directory for ``kind``, user layer first.

    Args:
        kind: Subdirectory name under both layers (``workflows``, ``agents``,
            ``prompts``, ``skills``).
        env_var: Optional environment variable that pins the directory.

    Returns:
        The user override directory when it exists, else the shipped defaults
        directory (which need not exist).
    """
    if env_var:
        override = os.environ.get(env_var)
        if override:
            return override
    for root in (user_dir(), checkout_root() / "defaults"):
        candidate = root / kind
        if candidate.is_dir():
            return str(candidate)
    return str(checkout_root() / "defaults" / kind)


# @stub-protected
def workflow_dirs() -> tuple[str, str | None]:
    """Workflow search roots as ``(shipped, local)``, for per-file lookup.

    :func:`config_dir` returns a single directory, so a repo-local
    ``.nebuko/workflows/`` would hide the shipped ``defaults/workflows/``
    entirely (issue #987). Callers that resolve a workflow *by name* need both
    layers, most-specific first — the same pair ``RunLoop._workflow_dirs``
    builds from :class:`nebuko.engine.paths.NebukoPaths`.

    Returns:
        ``(shipped, local)``: the shipped directory (pinned by
        ``WORKFLOWS_DIR`` when set) and the repo-local override directory, or
        ``None`` when it is absent or identical to the shipped one.
    """
    shipped = os.environ.get("WORKFLOWS_DIR") or str(
        checkout_root() / "defaults" / "workflows"
    )
    local = user_dir() / "workflows"
    if local.is_dir() and local.resolve() != Path(shipped).resolve():
        return shipped, str(local)
    return shipped, None


def config_path(name: str) -> str:
    """Resolve config ``name`` through the layered lookup.

    Args:
        name: A bare file name (for example ``agent-config.yaml``) or a path.

    Returns:
        The first existing match, else ``name`` unchanged when it is an existing
        path, else the shipped ``defaults/agents/<name>``.
    """
    if os.path.isabs(name) or os.path.isfile(name):
        return name
    for root in (user_dir(), checkout_root() / "defaults"):
        candidate = root / "agents" / name
        if candidate.is_file():
            return str(candidate)
    return str(checkout_root() / "defaults" / "agents" / name)


def agent_config_file() -> str:
    """Default agent-config path, pinned by ``AGENT_CONFIG_PATH`` when set."""
    return config_path(os.environ.get("AGENT_CONFIG_PATH") or "agent-config.yaml")
