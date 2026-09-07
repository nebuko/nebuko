"""Trigger parsing feature.

Pure string-parsing helpers that extract command flags and workflow state
from the raw trigger body, with no I/O.
"""

import re
import sys
from typing import Optional, Tuple

from workflow_state import extract_state_from_body

from ...sub_domains.trigger_commands.trigger_commands import TriggerCommands

__all__ = [
    "UNIVERSAL_BOT_MARKER",
    "is_bot_comment",
    "has_universal_bot_marker",
    "is_plain_mention_trigger",
    "extract_plain_mention_agent",
    "extract_mention_agent",
    "extract_machine_trigger",
    "extract_workflow_command",
    "extract_force_next_command",
    "extract_resume_command",
    "normalize_resume_input",
    "BARE_RESUME_INPUT",
    "extract_debug_flag",
    "_parse_trigger_body",
    "build_trigger_commands",
]

#: Universal bot marker prefixed to every CI-posted comment (issue #474).
UNIVERSAL_BOT_MARKER = "<!-- bot-workflow-terminal -->"


def has_universal_bot_marker(trigger_body: str) -> bool:
    """Check the first 5 lines for the universal bot marker.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if the universal marker is within the first 5 lines.
    """
    first_lines = "\n".join(trigger_body.split("\n")[:5])
    return UNIVERSAL_BOT_MARKER in first_lines


def is_bot_comment(trigger_body: str) -> bool:
    """Check if trigger body carries a bot marker.

    Primary check: the universal bot marker (<!-- bot-workflow-terminal -->)
    on any of the first 5 lines. Fallback (legacy): the agent marker
    (<!-- bot-agent-...) within the same window, for pre-existing issues
    posted before the universal marker was introduced.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if the body is identifiable as bot-generated.
    """
    first_lines = "\n".join(trigger_body.split("\n")[:5])
    return has_universal_bot_marker(trigger_body) or (
        "<!-- bot-agent-" in first_lines
    )


def is_plain_mention_trigger(trigger_body: str) -> bool:
    """Check if trigger body is a plain CI command trigger.

    A plain trigger consists solely of an @@mention plus optional
    HTML-comment markers (bot marker, subtask-complete, ...), with no
    other prose. This identifies CI-posted command triggers (e.g. the
    "@@genius" sub-issue bootstrap and the sub-task completion
    notification) that carry the universal bot marker but must still be
    routed to the mentioned agent — as opposed to agent answer comments,
    whose prose body disqualifies them here.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if the body is a mention plus markers only.

    Shape detection only: deliberately checks the raw body (not
    ``extract_mention_agent``), so CI machine triggers carrying the universal
    bot marker are recognized even though bot bodies never yield a routed
    mention (issue #478). Routing for those bodies still goes through the
    dedicated machine-token / ci_mention branches.
    """
    if extract_plain_mention_agent(trigger_body) is None:
        return False
    without_markers = re.sub(r"<!--.*?-->", " ", trigger_body, flags=re.DOTALL)
    without_mentions = re.sub(_MENTION_RE, " ", without_markers)
    return not without_mentions.strip()


def extract_plain_mention_agent(trigger_body: str) -> Optional[str]:
    """Extract the last @@mention from a trigger body without bot gating.

    Unlike :func:`extract_mention_agent`, this never refuses on bot markers;
    it is a shape helper used to pin the agent for CI plain-mention triggers
    that the ci_mention routing branch has already validated (issue #478).

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Last mentioned agent name, or None when no mention is present.
    """
    agents = re.findall(_MENTION_RE, trigger_body)
    if agents:
        return agents[-1].replace("@@", "")
    return None


_MENTION_RE = r"@@(?:issue-reviewer|genius|ctx|dev|review|issue|summarize)"


def extract_mention_agent(trigger_body: str) -> Optional[str]:
    """Extract the last @@mention agent name from a *human* trigger body.

    Bot comments (``<!-- bot-agent-... -->`` marker) NEVER yield a mention:
    agents communicate via the AGENT_RUN_CONFIG ``function_call`` section,
    and a stray ``@@`` in bot prose must not re-route execution (issue #478).
    Ignored mentions are logged to stderr for diagnosability.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Agent name string or None.
    """
    if is_bot_comment(trigger_body):
        ignored = re.findall(_MENTION_RE, trigger_body)
        if ignored:
            print(
                f"Warning: ignoring @@mention(s) {ignored} in bot comment "
                f"(bots must use function_call; issue #478)",
                file=sys.stderr,
            )
        return None
    agents = re.findall(_MENTION_RE, trigger_body)
    if agents:
        return agents[-1].replace("@@", "")
    return None


# Machine trigger tokens posted by the CI pipeline itself (issue #478).
# These replace the legacy machine-posted "@@genius" / "@@summarize" comments
# now that @@mentions are blocked for bot-triggered runs.
_SUBTASK_COMPLETE_RE = re.compile(r"<!--\s*bot-subtask-complete:\s*(\d+)\s*-->")
_SUMMARIZER_TRIGGER_RE = re.compile(r"<!--\s*bot-summarizer-trigger\s*-->")
_GENERIC_TRIGGER_RE = re.compile(r"<!--\s*bot-trigger:\s*([a-z][a-z-]*)\s*-->")


def extract_machine_trigger(trigger_body: str) -> Optional[Tuple[str, Optional[str]]]:
    """Extract a CI-posted machine trigger token from the trigger body.

    Recognized tokens:
      - ``<!-- bot-subtask-complete: N -->`` → ("genius", "N")
      - ``<!-- bot-summarizer-trigger -->``  → ("summarize", None)
      - ``<!-- bot-trigger: agent -->``      → ("agent", None) — generic form

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Tuple of (agent_name, extra) or None when no machine token is present.
        ``extra`` carries the sub-task index for the subtask-complete token.
    """
    match = _SUBTASK_COMPLETE_RE.search(trigger_body)
    if match:
        return ("genius", match.group(1))
    if _SUMMARIZER_TRIGGER_RE.search(trigger_body):
        return ("summarize", None)
    match = _GENERIC_TRIGGER_RE.search(trigger_body)
    if match:
        return (match.group(1), None)
    return None


def extract_workflow_command(trigger_body: str) -> Optional[str]:
    """Extract /workflow command from the first line of trigger body.

    Only matches if the first non-empty line starts with "/workflow <name>".
    Returns the workflow name or None.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Workflow name string or None.
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+(\S+)", first_line)
    if match:
        return match.group(1)
    return None


def extract_force_next_command(trigger_body: str) -> Optional[tuple]:
    """Extract '/workflow force-next-step' command from the first line.

    Supports optional target step: '/workflow force-next-step implement'.
    Returns ('force-next-step', target_step_or_none) or None.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Tuple of ('force-next-step', target_step) or None.
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+force-next-step(?:\s+(\S+))?", first_line)
    if match:
        target_step = match.group(1)  # Optional explicit target step name
        return ("force-next-step", target_step)
    return None


def extract_resume_command(trigger_body: str) -> Optional[str]:
    """Extract '/workflow resume' command from the first line.

    Returns the human-provided input text (everything after '/workflow resume')
    or None if the command is not present. If the command is present but has
    no trailing text, returns an empty string.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Human input text (str), empty string if no input, or None if not a resume command.
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+resume(?:\s+(.+))?$", first_line)
    if match:
        return match.group(1) if match.group(1) is not None else ""
    return None


#: Sentinel value for a bare `/workflow resume` (no feedback text). A bare
#: resume still carries the authorization signal for human_gate steps
#: (issue #506) — HUMAN_RESUME_INPUT must be non-empty so the gate
#: interceptor recognizes the run as human-triggered.
BARE_RESUME_INPUT = "__resumed__"


def normalize_resume_input(resume_text: Optional[str]) -> str:
    """Map raw /workflow resume text to the HUMAN_RESUME_INPUT value.

    A bare resume (None or blank text) yields the BARE_RESUME_INPUT sentinel;
    otherwise the stripped human text is returned unchanged.

    Args:
        resume_text: Result of extract_resume_command (may be None when the
            trigger is not a resume command).

    Returns:
        Human feedback text, or the sentinel for a bare resume.
    """
    if resume_text is None:
        return ""
    text = resume_text.strip()
    return text if text else BARE_RESUME_INPUT


def extract_debug_flag(trigger_body: str) -> bool:
    """Extract DEBUG=true flag from trigger body.

    Looks for 'DEBUG=true' (case-insensitive) anywhere in the comment body,
    including as part of /workflow commands like '/workflow default DEBUG=true'.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if DEBUG=true is found, False otherwise.
    """
    return bool(re.search(r"DEBUG\s*=\s*true", trigger_body, re.IGNORECASE))


# ── Helper methods for resolve_config ──────────────────────────────────────



def _parse_trigger_body(trigger_body: str) -> tuple:
    """Extract workflow state and dispatch from the current trigger body.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Tuple of (prev_state, dispatch_obj).
    """
    prev_state = extract_state_from_body(trigger_body)
    dispatch_obj = prev_state.dispatch if prev_state else None
    return (prev_state, dispatch_obj)


def build_trigger_commands(body: str) -> TriggerCommands:
    """Bundle all command flags extracted from a trigger body.

    Convenience wrapper returning a single typed value object instead of
    calling each extractor separately.

    Args:
        body: Raw trigger body text.

    Returns:
        TriggerCommands with all extracted fields.
    """
    return TriggerCommands(
        is_bot=is_bot_comment(body),
        mention_agent=extract_mention_agent(body),
        workflow_cmd=extract_workflow_command(body),
        force_next=extract_force_next_command(body),
        resume=extract_resume_command(body),
        debug=extract_debug_flag(body),
    )

