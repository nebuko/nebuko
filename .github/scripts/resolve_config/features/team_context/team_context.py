"""Team context feature — build and export cross-cutting prompt sections.

Generates the "Your Team" section (roster, delegation rules, call-back rule),
the shared exit rules, and the dynamic "you were called by …" line, written to
a single maintained file that ``agent_script.py`` injects into every agent
prompt (issue #476). Data comes from the agent roster in agent-config.yaml;
when the config is unavailable the features degrade to empty values and no
section is injected.
"""

import json
import os
from typing import Optional

from config import extract_caller_agent, load_config

__all__ = [
    "build_team_env",
    "load_team_data",
    "load_agent_skills",
    "build_shared_context",
    "default_config_path",
]

# Default hand-off location for the generated shared-context file.
SHARED_CONTEXT_DEFAULT_PATH = "/tmp/shared_context.md"


def default_config_path() -> str:
    """Return the agent-config.yaml path, resolved to the repo root.

    The resolver and the workflow run from different working directories, so a
    plain relative path is not portable. Walk up from this file to the nearest
    ``agent-config.yaml`` (the repo root); fall back to the relative name so a
    caller in the repo root can still supply its own.
    """
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        candidate = os.path.join(current, "agent-config.yaml")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return "agent-config.yaml"


_EXIT_RULES = """\
# Strict Output & Exit Rules (shared)

* **Rule A (Mandatory Feedback):** You must ALWAYS end your session with a clear status update — never exit silently.
* **Rule B (Hand-off Command):** To hand off to the next step, end your final message with an `AGENT_RUN_CONFIG` sentinel:
  ```
  <!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "medium"} -->
  ```
  Replace `"medium"` with `"low"` or `"high"` as appropriate, and add `"pr_number": <N>` for dev hand-offs. No explicit `task` field is needed — the workflow engine resolves the next step from the YAML definition.
* **Rule C (No Premature Completion):** Never claim "task complete" without doing the work or delegating it. If blocked, explain the blocker and hand off."""


def _delegation_rules() -> str:
    """Return the delegation rules block of the team section."""
    return (
        "Delegation rules:\n"
        "* You may only perform work covered by your own available actions "
        "(your tool whitelist). Work in a teammate's scope must be delegated by "
        "calling them with the `@@<name>` annotation.\n"
        "* When a teammate calls you, you must call them back with your answer "
        "before exiting — treat it as a conversation turn, not a fire-and-forget."
    )


def build_team_section(roles: dict[str, str], self_name: str) -> str:
    """Render the 'Your Team' section from the roster roles.

    Args:
        roles: Mapping of agent name to one-line role description.
        self_name: Name of the agent being prompted ("" → generic header).

    Returns:
        Markdown text of the team section ("" if no roles).
    """
    if not roles:
        return ""

    lines = ["# Your Team", ""]
    if self_name:
        self_role = roles.get(self_name, "")
        lines.append(f"You are @@@{self_name}" + (f" — {self_role}" if self_role else ""))
        teammates = [n for n in roles if n != self_name]
    else:
        teammates = list(roles)
    if teammates:
        lines.append("Teammates:")
        for name in teammates:
            role = roles.get(name, "")
            lines.append(f"- @@@{name}: {role}" if role else f"- @@@{name}")
    lines.append("")
    lines.append(_delegation_rules())
    return "\n".join(lines)


def build_caller_line(caller: str, self_name: str = "") -> str:
    """Render the dynamic call-back line for a bot-invoked run.

    Args:
        caller: Name of the agent that invoked this run ("" if none).
        self_name: Name of the agent being prompted.

    Returns:
        Markdown line, or "" when there is no caller (or self-call).
    """
    if not caller or caller == self_name:
        return ""
    return f"You were called by @@@{caller} — call @@@{caller} back with your result."


def build_shared_context(
    roles: dict[str, str],
    self_name: str,
    caller: str,
    tools: list[str],
) -> str:
    """Compose the full shared prompt section (team + call-back + exit rules).

    Args:
        roles: Mapping of agent name to role description.
        self_name: Name of the agent being prompted.
        caller: Name of the invoking agent ("" if human-triggered).
        tools: Tool whitelist of the agent being prompted.

    Returns:
        Complete Markdown section, or "" when no roles are known (the roster
        is the anchor — without it, no shared section is injected).
    """
    if not roles:
        return ""

    parts = [build_team_section(roles, self_name)]
    if tools:
        parts.append(f"# Your Available Actions\n\nYou may only use these tools: {', '.join(tools)}.")
    caller_line = build_caller_line(caller, self_name)
    if caller_line:
        parts.append(caller_line)
    parts.append(_EXIT_RULES)
    return "\n\n".join(p for p in parts if p).strip() + "\n"


def load_team_data(config_path: Optional[str] = None) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Load roles and tool whitelists from the layered config.

    Args:
        config_path: Path to the agent-config.yaml file. Defaults to the
            repo-root copy when it exists (see ``default_config_path``).

    Returns:
        Tuple of ({name: role}, {name: [tools]}). Both mappings are empty
        when the config is missing or malformed.
    """
    try:
        app_config = load_config(config_path or default_config_path())
    except Exception:  # noqa: BLE001 — missing/bad config degrades to no team section
        return {}, {}

    roles = {
        name: getattr(agent, "role", None) or ""
        for name, agent in (app_config.agents or {}).items()
    }
    tools = {
        name: list(getattr(agent, "tools", []) or [])
        for name, agent in (app_config.agents or {}).items()
    }
    return roles, tools


def load_agent_skills(
    agent_name: str, config_path: Optional[str] = None
) -> str:
    """Return the default skills of one agent as a comma-separated list.

    Reads the ``skills`` field of the named agent from agent-config.yaml.
    Used to flow per-agent default skills into ``AGENT_SKILLS`` so the
    workflow's skill-file plumbing picks them up (issue #476).

    Args:
        agent_name: Name of the agent (roster key).
        config_path: Path to the agent-config.yaml file; defaults to the
            repo-root copy (see ``default_config_path``).

    Returns:
        Comma-separated skill names, or "" when the config is missing or
        the agent declares no skills.
    """
    try:
        app_config = load_config(config_path or default_config_path())
    except Exception:  # noqa: BLE001 — degrades to no default skills
        return ""
    agents = app_config.agents or {}
    agent = agents.get(agent_name)
    if agent is None:
        return ""
    return ",".join(getattr(agent, "skills", []) or [])


def _agent_tools(role_name: str, tools_by_agent: dict[str, list[str]]) -> list[str]:
    """Return the tool whitelist of one agent as a list."""
    return tools_by_agent.get(role_name, [])


def build_team_env(
    trigger_body: str,
    role_name: Optional[str] = None,
    config_path: Optional[str] = None,
    shared_context_path: str = SHARED_CONTEXT_DEFAULT_PATH,
) -> dict[str, str]:
    """Build the team-context environment values for one agent run.

    Also writes the shared-context markdown (team section + call-back line +
    shared exit rules) to ``shared_context_path`` when roles are known.

    Args:
        trigger_body: Raw trigger/comment body (caller marker source).
        role_name: Name of the agent about to run.
        config_path: Path to the agent-config.yaml file. Defaults to the
            repo-root copy when it exists (see ``default_config_path``).
        shared_context_path: Where to write the generated section file.

    Returns:
        Dict with ``AGENT_ROLES`` (JSON or ""), ``AGENT_TOOLS`` (comma list
        or ""), ``CALLER_AGENT`` (marker value or "") and
        ``SHARED_CONTEXT_FILE`` (file path or "").
    """
    roles, tools_by_agent = load_team_data(config_path)
    caller = extract_caller_agent(trigger_body) or ""
    section = build_shared_context(
        roles, role_name or "", caller, _agent_tools(role_name or "", tools_by_agent)
    )
    shared_context_file = ""
    if section:
        with open(shared_context_path, "w", encoding="utf-8") as f:
            f.write(section)
        shared_context_file = shared_context_path

    return {
        "AGENT_ROLES": json.dumps(roles) if roles else "",
        "AGENT_TOOLS": ",".join(_agent_tools(role_name or "", tools_by_agent)),
        "CALLER_AGENT": caller,
        "SHARED_CONTEXT_FILE": shared_context_file,
    }
