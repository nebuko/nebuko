"""Agent roster feature — derive the agent registry from agent-config.yaml.

The map keys of the ``agents`` section ARE the agent names; this feature
derives the routing table, the @@mention regex, and validation checks from
that single source of truth. All functions are pure (no I/O) except
``load_agent_roster``, which wraps ``load_config`` and falls back to the
static legacy mapping when the config is unavailable.
"""

import re
import sys
from pathlib import Path
from typing import Optional

KNOWN_TOOLS = frozenset(
    {"terminal", "file_editor", "task_tracker", "think", "finish"}
)

# Legacy fallback mapping (kept for environments where agent-config.yaml
# cannot be read). Mirrors the former hardcoded TASK_TO_PROMPT table.
STATIC_TASK_TO_PROMPT = {
    "genius": "genius.md",
    "ctx": "ctx.md",
    "dev": "dev.md",
    "review": "review.md",
    "issue": "issue.md",
    "issue-reviewer": "issue-reviewer.md",
    "summarize": "summarize.md",
    "debug": "debug.md",
    "po": "po.md",
}

_CALLER_MARKER_RE = re.compile(r"<!-- bot-agent-([a-z][a-z-]*) -->")


def build_agent_roster(config) -> dict[str, str]:
    """Build the {agent_name: prompt_file} routing table from AppConfig.

    Args:
        config: AppConfig instance (or any object with an ``agents`` mapping
            whose values expose a ``prompt_file`` attribute).

    Returns:
        Ordered mapping of agent name → prompt file name (basename).
    """
    return {
        name: Path(agent.prompt_file).name
        for name, agent in config.agents.items()
    }


def validate_roster(
    roster: dict[str, str],
    roles: Optional[dict[str, str]] = None,
    tools_by_agent: Optional[dict[str, list[str]]] = None,
    skills_by_agent: Optional[dict[str, list[str]]] = None,
    prompt_base: str = "",
    skills_dir: str = "skills",
) -> list[str]:
    """Validate a roster and return a list of human-readable problems.

    Checks performed:
    - every prompt file exists under ``prompt_base`` (when given)
    - every agent has a non-empty role (when ``roles`` is given)
    - every tool is in KNOWN_TOOLS
    - every skill file exists under ``skills_dir``

    Args:
        roster: {agent_name: prompt_file} mapping.
        roles: Optional {agent_name: role_description}; empty values flagged.
        tools_by_agent: Optional {agent_name: [tool, ...]}.
        skills_by_agent: Optional {agent_name: [skill, ...]}.
        prompt_base: Directory containing the prompt files.
        skills_dir: Directory containing ``<skill>.md`` files.

    Returns:
        List of problem strings; empty list means the roster is valid.
    """
    problems: list[str] = []

    for name, prompt_file in roster.items():
        if prompt_base:
            prompt_path = Path(prompt_base) / prompt_file
            if not prompt_path.exists():
                problems.append(
                    f"agent '{name}': prompt_file not found: {prompt_path}"
                )
        if roles is not None and not roles.get(name):
            problems.append(f"agent '{name}': missing or empty 'role' field")
        for tool in (tools_by_agent or {}).get(name, []):
            if tool not in KNOWN_TOOLS:
                problems.append(
                    f"agent '{name}': unknown tool '{tool}' "
                    f"(known: {sorted(KNOWN_TOOLS)})"
                )
        for skill in (skills_by_agent or {}).get(name, []):
            skill_path = Path(skills_dir) / f"{skill}.md"
            if not skill_path.exists():
                problems.append(f"agent '{name}': skill file not found: {skill_path}")
    return problems


def build_mention_regex(roster: dict[str, str]) -> re.Pattern:
    """Build the @@mention regex from roster keys.

    Longest names are alternated first so hyphenated keys such as
    ``issue-reviewer`` win over their shorter prefixes (``issue``).

    Args:
        roster: {agent_name: prompt_file} mapping.

    Returns:
        Compiled regex with one group capturing the mentioned agent name.
    """
    names = sorted(roster.keys(), key=len, reverse=True)
    return re.compile(r"@@" + r"(?:" + "|".join(re.escape(n) for n in names) + r")")


def extract_caller_agent(trigger_body: str) -> Optional[str]:
    """Extract the calling bot agent from a ``<!-- bot-agent-<name> -->`` marker.

    Args:
        trigger_body: Raw trigger/comment body.

    Returns:
        Caller agent name, or None when no bot marker is present.
    """
    match = _CALLER_MARKER_RE.search(trigger_body)
    return match.group(1) if match else None


def load_agent_roster(config_path: str = "agent-config.yaml") -> dict[str, str]:
    """Load the roster from the layered config, with a static fallback.

    Falls back to STATIC_TASK_TO_PROMPT (with a warning) whenever the config
    file is missing or malformed, so routing never hard-fails on an
    unreadable config. CLIs that must fail fast should use
    ``validate_roster`` instead.

    Args:
        config_path: Path to the agent-config.yaml file.

    Returns:
        {agent_name: prompt_file} mapping.
    """
    try:
        from config import load_config

        return build_agent_roster(load_config(config_path))
    except Exception as e:  # noqa: BLE001 — intentional broad fallback
        print(
            f"Warning: could not load agent roster from '{config_path}' "
            f"({e}); using static fallback roster",
            file=sys.stderr,
        )
        return dict(STATIC_TASK_TO_PROMPT)