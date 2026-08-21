"""Shared context injection — single maintained copies of cross-cutting prompt sections.

Generates the sections that were previously duplicated verbatim in every
agent prompt file (strict output/exit rules) plus the "Your Team" section
that makes each agent aware of its teammates and the delegation / call-back
protocol (issue #476). All functions are pure; environment access is
isolated to ``build_shared_context``.
"""

import json
import os
import re
from typing import Optional

_BOT_AGENT_MARKER_RE = re.compile(r"<!-- bot-agent-([a-z][a-z-]*) -->")

# The marker line every prompt file carries before the user request.
USER_REQUEST_ANCHOR = "\n--- USER REQUEST / GITHUB CONTEXT ---\n"

# Legacy per-file section header removed at injection time so agents only
# ever see the single maintained copy.
LEGACY_EXIT_RULES_HEADER = "# Strict Output & Exit Rules"


SHARED_EXIT_RULES = """\
# Strict Output & Exit Rules

* **Rule A (Mandatory Feedback):** You must ALWAYS end your session with a clear status update (via the `finish` tool when available, or as the final lines of your message). Never exit silently.
* **Rule B (Hand-off Command):** When a workflow hand-off is expected, end your final message with an `AGENT_RUN_CONFIG` sentinel using the format given by your role-specific instructions. No explicit `task` field is needed unless the format specifies one — the workflow engine resolves the next step from the YAML definition; agents signal completion via the `dispatch` status.
* **Rule C (Scope Enforcement):** Only perform work covered by your own available actions (tools: {tools}). Work in a teammate's scope must be delegated by calling them with the @@<name> annotation — never attempt it yourself.

### AGENT_RUN_CONFIG Sentinel Format

```
<!-- AGENT_RUN_CONFIG {{"dispatch": {{"status": "advance"}}, ...}} -->
```

Common payload fields: `dispatch.status` (required, `"advance"`), `complexity` (`"low"`/`"medium"`/`"high"`), `round`, `pr_number`, `branch`, `pin`.
"""


def render_shared_exit_rules(tools: list[str]) -> str:
    """Render the shared exit-rules section with the agent's tool list.

    Args:
        tools: Tool whitelist for the current agent (e.g. ["think", "finish"]).

    Returns:
        The markdown section text.
    """
    return SHARED_EXIT_RULES.format(tools=", ".join(tools) if tools else "none")


def _team_block(self_name: str, roles: dict[str, str], tools: list[str]) -> str:
    """Build the "Your Team" markdown block.

    Args:
        self_name: Name of the agent being prompted.
        roles: {agent_name: role} mapping (may include the agent itself).
        tools: Tool whitelist for the current agent.

    Returns:
        The markdown block text.
    """
    lines: list[str] = ["# Your Team"]
    self_role = roles.get(self_name, "")
    lines.append(f"You are @@@{self_name}" + (f" — {self_role}" if self_role else ""))
    teammates = [
        f"- @@@{name}: {role}" if role else f"- @@@{name}"
        for name, role in roles.items()
        if name != self_name
    ]
    if teammates:
        lines.append("Teammates:")
        lines.extend(teammates)
    lines.append("Delegation rules:")
    lines.append(
        "- You may only perform work covered by your own available actions "
        f"(tools: {', '.join(tools) if tools else 'none'}). Work in a teammate's scope "
        "must be delegated by calling them with the @@@<name> annotation."
    )
    lines.append(
        "- When a teammate calls you, you must call them back with your answer "
        "before exiting — treat it as a conversation turn, not a fire-and-forget."
    )
    return "\n".join(lines)


def detect_caller(trigger_body: Optional[str]) -> Optional[str]:
    """Extract the calling agent from a ``<!-- bot-agent-<name> -->`` marker.

    Args:
        trigger_body: Raw trigger/comment body (or None).

    Returns:
        Caller agent name, or None when no bot marker is present.
    """
    if not trigger_body:
        return None
    match = _BOT_AGENT_MARKER_RE.search(trigger_body)
    return match.group(1) if match else None


def build_shared_context(
    role_name: Optional[str],
    trigger_body: Optional[str] = None,
) -> str:
    """Build the shared sections to inject into an agent's prompt.

    Reads ``AGENT_ROLES`` (JSON ``{name: role}``), ``AGENT_TOOLS``
    (comma-separated) and ``CALLER_AGENT`` from the environment, falling back
    to the caller marker parsed from ``trigger_body``. Degrades gracefully to
    an empty string when no team information is available.

    Args:
        role_name: Name of the agent being prompted (``ROLE_NAME``).
        trigger_body: Trigger/comment body, used as a fallback caller source.

    Returns:
        Concatenated markdown sections, or "" if nothing can be built.
    """
    roles_raw = os.environ.get("AGENT_ROLES", "")
    try:
        roles = json.loads(roles_raw) if roles_raw else {}
        if not isinstance(roles, dict):
            roles = {}
    except (json.JSONDecodeError, TypeError):
        roles = {}

    tools = [
        t.strip()
        for t in os.environ.get("AGENT_TOOLS", "").split(",")
        if t.strip()
    ]

    if not role_name or not roles:
        return ""

    caller = os.environ.get("CALLER_AGENT", "").strip() or detect_caller(trigger_body)

    sections = [
        render_shared_exit_rules(tools),
        _team_block(role_name, roles, tools),
    ]
    if caller and caller != role_name:
        sections.append(
            f"You were called by @@@{caller} — call @@@{caller} back with your result."
        )
    return "\n\n".join(sections)


def _strip_legacy_exit_rules(prompt: str) -> str:
    """Remove a legacy per-file exit-rules block from the prompt.

    The block runs from the legacy header to either the next top-level
    ``# `` heading or the user-request anchor, whichever comes first.

    Args:
        prompt: The prompt text.

    Returns:
        Prompt without the legacy block.
    """
    idx = prompt.find(LEGACY_EXIT_RULES_HEADER)
    if idx == -1:
        return prompt
    tail = prompt[idx + len(LEGACY_EXIT_RULES_HEADER):]
    end_match = re.search(r"\n# |" + re.escape(USER_REQUEST_ANCHOR), tail)
    end = (idx + len(LEGACY_EXIT_RULES_HEADER) + end_match.start()) if end_match else len(prompt)
    return prompt[:idx] + prompt[end:]


def inject_shared_context(
    prompt: str,
    role_name: Optional[str],
    trigger_body: Optional[str] = None,
) -> str:
    """Inject the shared sections into a prompt exactly once.

    Removes any legacy per-file exit-rules block first, then inserts the
    single maintained copy just before the user-request anchor (or at the end
    when the anchor is absent).

    Args:
        prompt: The assembled prompt (file content + skills + user request).
        role_name: Name of the agent being prompted.
        trigger_body: Trigger/comment body (fallback caller source).

    Returns:
        The prompt with the shared sections injected.
    """
    prompt = _strip_legacy_exit_rules(prompt)
    shared = build_shared_context(role_name, trigger_body)
    if not shared:
        return prompt.rstrip() + "\n"

    if USER_REQUEST_ANCHOR in prompt:
        head, tail = prompt.split(USER_REQUEST_ANCHOR, 1)
        return head.rstrip() + "\n\n" + shared + "\n" + USER_REQUEST_ANCHOR + tail
    return prompt.rstrip() + "\n\n" + shared + "\n"