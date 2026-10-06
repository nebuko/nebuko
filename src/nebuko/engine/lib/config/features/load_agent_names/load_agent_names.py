"""Load agent names feature module.

Derives the invocable-agent roster from the layered agent config so that
``@@`` mention parsing stays in sync with the agents actually registered —
adding an agent to ``agent-config.yaml`` is enough to make it mentionable.
"""

import os

from ...config import CONFIG_DEFAULT_PATH
from ...features.load_config.load_config import load_config

#: Canonical roster used when the agent config cannot be read. It is never the
#: source of truth (the config is) — it only keeps mention parsing from crashing
#: a run when the file is missing or malformed.
DEFAULT_AGENT_NAMES: frozenset[str] = frozenset({
    "genius", "ctx", "dev", "review", "issue",
    "issue-reviewer", "summarize", "debug", "po",
})

_names_cache: dict[str, frozenset[str]] = {}


def load_agent_names(path: str = CONFIG_DEFAULT_PATH) -> frozenset[str]:
    """Agent names declared in the layered agent config (config.agents.keys()).

    Default path is $AGENT_CONFIG_PATH (falling back to agent-config.yaml).
    Any failure (missing file, unresolvable refs, bad schema) returns
    DEFAULT_AGENT_NAMES so mention parsing never crashes a run. Result cached.
    """
    effective_path = path
    if path == CONFIG_DEFAULT_PATH:
        effective_path = os.environ.get("AGENT_CONFIG_PATH", CONFIG_DEFAULT_PATH)

    cached = _names_cache.get(effective_path)
    if cached is not None:
        return cached

    try:
        # A valid config may declare an explicitly empty ``agents:`` map; an
        # empty roster would turn the derived mention pattern into a bare ``@@``
        # matcher, so fall back to the canonical roster in that case too.
        names = frozenset(load_config(effective_path).agents.keys()) or DEFAULT_AGENT_NAMES
    except Exception:  # noqa: BLE001 - mention parsing must be non-fatal
        names = DEFAULT_AGENT_NAMES

    _names_cache[effective_path] = names
    return names
