"""Team context feature module (issue #476).

Renders the shared sections every agent's system prompt carries: the generic
exit discipline (removed from the individual prompt files, which now hold only
personality and core rules) and the generated "Your Team" section that tells an
agent who its teammates are and how to delegate to them.

The roster is read from the layered agent config, so adding an agent to
``agent-config.yaml`` is enough for it to appear in every other agent's team
section — no prompt edit required.
"""

import os
from dataclasses import dataclass
from typing import Optional

from config import CONFIG_DEFAULT_PATH, load_config
from config.sub_domains.app_config.app_config import AppConfig

#: Generic exit discipline, injected once into every built system prompt.
#: Role- and mode-specific hand-off contracts stay with the role/skill.
SHARED_EXIT_RULES = """# Strict Output & Exit Rules

* **Rule A (Mandatory Feedback):** Always end your session explicitly — never exit
  silently. Your last free-form text answer is the deliverable that gets posted to
  the issue, so make it complete and self-contained (see the `posting-contract`
  skill).
* **Rule B (Typed Hand-off Only):** Your `dispatch_next` / `call_agent` tool
  call is the only hand-off the engine reads. Never write an
  `AGENT_RUN_CONFIG` sentinel (or any other prose marker) to signal routing —
  the engine ignores it and the typed call is the sole routing fact.
* **Rule C (Never End Without a Decision):** Terminate through `finish` or
  `dispatch_next`. An ending that carries neither triggers an engine recall pass
  whose short status note can clobber your posted answer.
"""

#: Placeholder used when a roster entry declares no ``role`` (see the C8 lint rule).
UNDECLARED_ROLE = "role not declared"


@dataclass(frozen=True)
class TeammateInfo:
    """One other agent of the roster, as seen from a teammate's prompt."""

    name: str
    role: str
    tools: tuple[str, ...]


@dataclass(frozen=True)
class TeamContext:
    """The "Your Team" section of an agent's system prompt.

    Built from the live roster (:meth:`from_config`) and rendered to Markdown
    (:meth:`render`). It never names the caller (issue #1033): the return
    address is engine-owned run state the callee must not see or depend on.
    """

    self_name: str
    self_role: str
    teammates: tuple[TeammateInfo, ...]
    # Deprecated no-op kept for call-site compatibility (issue #1033): the
    # callback is engine-owned and never rendered into the callee's context.
    caller: Optional[str] = None
    self_tools: tuple[str, ...] = ()

    # @stub-protected
    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        self_name: str,
        caller: Optional[str] = None,
    ) -> "TeamContext":
        """Build the team view for ``self_name`` from a loaded agent config.

        Raises:
            KeyError: If ``self_name`` is not part of the roster.
        """
        agent = config.agents[self_name]
        teammates = tuple(
            TeammateInfo(
                name=name,
                role=entry.role or UNDECLARED_ROLE,
                tools=tuple(entry.tools),
            )
            for name, entry in sorted(config.agents.items())
            if name != self_name
        )
        return cls(
            self_name=self_name,
            self_role=agent.role or UNDECLARED_ROLE,
            teammates=teammates,
            caller=caller or None,
            self_tools=tuple(agent.tools),
        )

    # @stub-protected
    def render(self) -> str:
        """Render the team section as Markdown."""
        lines = ["# Your Team", f"You are {self.self_name} — {self.self_role}"]
        if self.teammates:
            lines.append("Teammates:")
            lines.extend(f"- {mate.name}: {mate.role}" for mate in self.teammates)
        lines.append("Delegation rules:")
        lines.append(
            f"- Only do work covered by your own tools ({_tools(self.self_tools)}). "
            "Work in a teammate's scope must be delegated to them via the "
            "`call_agent` mechanism (see the `call-agent` skill); `@@<name>` "
            "mentions are for humans only."
        )
        lines.append(
            "- When a teammate calls you, your answer is returned to them automatically "
            "(the engine's `callback` routes it) — treat it as a conversation turn, "
            "not fire-and-forget."
        )
        return "\n".join(lines) + "\n"


def _tools(tools: tuple[str, ...]) -> str:
    """Comma-joined tool list, with a readable fallback for an empty whitelist."""
    return ", ".join(tools) if tools else "no tools"


# @stub-protected
def build_team_section(
    self_name: str,
    caller: Optional[str] = None,
    config_path: Optional[str] = None,
) -> str:
    """Render the team section for ``self_name``, or ``""`` when unavailable.

    Never raises: an unreadable roster or an unknown ``self_name`` degrades to no
    section so the agent still boots (same non-fatal contract as
    ``config.load_agent_names``).
    """
    if not self_name:
        return ""
    if config_path is None:
        config_path = os.environ.get("AGENT_CONFIG_PATH", CONFIG_DEFAULT_PATH)
    try:
        config = load_config(config_path)
        return TeamContext.from_config(config, self_name, caller).render()
    except Exception:  # noqa: BLE001 - a missing team section must not stop a run
        return ""


# @stub-protected
def compose_system_context(
    self_name: str,
    caller: Optional[str] = None,
    config_path: Optional[str] = None,
) -> str:
    """The full injected block: shared exit rules plus the team section."""
    sections = [SHARED_EXIT_RULES, build_team_section(self_name, caller, config_path)]
    return "\n".join(section for section in sections if section)
