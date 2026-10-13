"""Prepare github_comment.md from final_message.txt.

Reads the agent's final message, strips internal XML/HTML tags
(AGENT_RUN_CONFIG sentinels + legacy <agentRunConfig>), and writes the cleaned
content to github_comment.md for the peter-evans/comment action.

All regex operations use re.DOTALL for robust multi-line matching.
No bash intermediate — Python reads and writes files directly.

Handles multiple paths mirroring the workflow logic:
  Path A: Structured final_message.txt (happy path)
  Path B: Fallback to console.txt parsing
  Path C: Agent crashed before producing output
  Path D: final_message.txt doesn't exist
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import TYPE_CHECKING

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_output import AGENT_RUN_CONFIG_PATTERN, strip_agent_run_config
from workflow_state.features.comment.to_git_comment import (
    collapse_sections,
    format_collapsed_section,
)

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only (issue #590)
    from nebuko.adapters.base import Poster
    from workflow_state.workflow_state import WorkflowState

# ── Regex patterns (all DOTALL for multi-line safety) ─────────────

# Legacy <agentRunConfig> XML block — comment hygiene ONLY.
# The legacy form is no longer a dispatch-parsing path (issue #450);
# this strip prevents stale raw XML from leaking into rendered comments.
_LEGACY_AGENT_RUN_CONFIG = re.compile(r"<agentRunConfig>.*?</agentRunConfig>", re.DOTALL)

# Console.log "Message from Agent" separator
_CONSOLE_MARKER = "Message from Agent " + "\u2500" * 63

# @@agent trigger tokens (e.g. @@architect, @@dev)
_AGENT_TRIGGER = re.compile(r"(?<![\w])@@([A-Za-z0-9_-]+)")

# Bare human @mentions in *agent-authored* bodies (issue #795). Notification is
# the engine's job now, so an agent must never be able to ping a human.
#
# Code regions are matched first and emitted verbatim: GitHub does not notify
# for a mention inside fenced or inline code either, so rewriting one there would
# only corrupt the snippet (``@dataclass`` -> ``@\u200bdataclass``). A mention
# glued to a word (``user@host``) is not a notification for the same reason.
_CODE_REGION = r"```.*?```|~~~.*?~~~|`[^`\n]*`"
_HUMAN_MENTION = re.compile(
    rf"(?P<code>{_CODE_REGION})"
    r"|(?<![\w@])@(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)",
    re.DOTALL,
)

MAX_COMMENT_LENGTH = 60000

# Section headings of the context report template (.agents/prompts/context.md).
# Wrapped in <details> at posting time so a long context report stays scannable
# while remaining fully auditable in the comment body (issue #557).
CONTEXT_SECTION_HEADINGS = [
    "FILES FOUND",
    "RELEVANT SNIPPETS",
    "EXTERNAL REFERENCES",
    "OTHER FILES",
]

# Artifact written by the capture layer in build_output.py (issue #522):
# the agent's latest full answer, preferred over the finish message as the
# posted-comment body.
LATEST_ANSWER_FILE = "latest_answer.md"

# Artifact written by the agent child (build_output.py): the run's authoritative
# WorkflowState, whose collapsed form is the "answer" comment's state section.
WORKFLOW_STATE_FILE = "workflow_state.json"


# ── Latest-answer ETL (issue #522) ───────────────────────────────


def select_posted_body(latest_answer: str, final_message: str) -> str:
    # @stub-protected: signature defined by architect — do not modify
    """Select the posted-comment body from the two candidate sources.

    Contract (ETL layer, uniform for every agent):
      * ``latest_answer`` non-empty after stripping → it is the body
        (the full deliverable).
      * Otherwise → ``final_message`` (the finish message; identical to
        the pre-#522 behavior).

    Args:
        latest_answer: Raw content of latest_answer.md (may be "").
        final_message: Raw final_message.txt content (may be "").

    Returns:
        The raw body text to clean and post.
    """
    stripped = latest_answer.strip()
    return stripped if stripped else final_message


def merge_handoff_sentinels(*sources: str) -> str:
    # @stub-protected: signature defined by architect — do not modify
    """Merge AGENT_RUN_CONFIG hand-off sentinels from the candidate sources.

    Contract (ETL invariant — dispatch/branching must always reach the
    comment):
      * Scan sources left to right; the LAST sentinel payload found wins
        (mirrors extract_dispatch_resilient's last-valid-payload rule).
      * The winning sentinel block is returned exactly once — deduped no
        matter how many times / in how many sources it appears.
      * Return "" when no source contains a sentinel.

    Args:
        *sources: Raw text sources, e.g. (latest_answer, final_message).

    Returns:
        A single "<!-- AGENT_RUN_CONFIG ... -->" block, or "".
    """
    last_payload = ""
    for source in sources:
        if not source:
            continue
        for match in AGENT_RUN_CONFIG_PATTERN.finditer(source):
            last_payload = match.group(1)
    if not last_payload:
        return ""
    return f"<!-- AGENT_RUN_CONFIG {last_payload} -->"


# Typed sections rendered into the engine-owned hand-off sentinel, in the
# canonical ``AgentRunConfig`` shape (issue #830). Each entry is
# ``(sentinel_key, model_attribute)``; only truthy fields are carried, so an
# unset field is omitted and the payload stays minimal and JSON-stable. The
# dispatch model names the cross-agent target ``task_hint``; the sentinel
# spells that routing field ``agent`` (what ``to_routing_decision`` reads).
_DISPATCH_SENTINEL_FIELDS = (
    ("status", "status"),
    ("branch", "branch"),
    ("complexity", "complexity"),
    ("reason", "reason"),
    ("workflow", "workflow"),
    ("step", "step"),
    ("agent", "task_hint"),
    ("pin", "pin"),
)
_FUNCTION_CALL_SENTINEL_FIELDS = (
    ("function", "function"),
    ("agent_name", "agent_name"),
    ("complexity", "complexity"),
    ("reason", "reason"),
    ("callback", "callback"),
)


def render_handoff_sentinel(state: WorkflowState | None) -> str:
    # @stub-protected: signature defined by architect — do not modify
    """Render the AGENT_RUN_CONFIG sentinel from the run's typed dispatch.

    The engine owns the routing decision as structured data
    (``workflow_state.dispatch`` / ``function_call``); rendering the hand-off
    sentinel from those fields — instead of scraping it out of agent prose —
    makes the posted sentinel deterministic (issue #830). Return "" when the
    state carries no decision, so the legacy scrape still applies.

    Args:
        state: The run's ``workflow_state.json`` artifact, or ``None``.

    Returns:
        A single "<!-- AGENT_RUN_CONFIG ... -->" block, or "".
    """
    payload = _handoff_sentinel_payload(state)
    if payload is None:
        return ""
    return f"<!-- AGENT_RUN_CONFIG {json.dumps(payload)} -->"


def _handoff_sentinel_payload(state: WorkflowState | None) -> dict[str, object] | None:
    """The canonical sentinel payload from the state's typed decision, or None."""
    if state is None:
        return None
    payload: dict[str, object] = {}
    dispatch = _populated_fields(getattr(state, "dispatch", None), _DISPATCH_SENTINEL_FIELDS)
    if dispatch:
        payload["dispatch"] = dispatch
    function_call = _populated_fields(
        getattr(state, "function_call", None), _FUNCTION_CALL_SENTINEL_FIELDS
    )
    if function_call:
        payload["function_call"] = function_call
    return payload or None


def _populated_fields(
    source: object, fields: tuple[tuple[str, str], ...]
) -> dict[str, object]:
    """The truthy ``(key, attribute)`` fields of a pydantic section, as a dict."""
    if source is None:
        return {}
    return {
        key: value
        for key, attribute in fields
        if (value := getattr(source, attribute, None))
    }


def _read_latest_answer(path: str = LATEST_ANSWER_FILE) -> str:
    """Read the latest-answer artifact; return "" if missing/unreadable."""
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _warn_post_recall_fallback(recall_count: int) -> None:
    """Surface the post-recall finish-fallback case in CI logs (issue #558).

    Fires when the posted body came from the finish message although the
    run went through a dispatch-recall pass — a signature of the
    clobber/fallback family of bugs. The count comes from the run's
    workflow state (``history[-1].metrics.dispatch_recall_count``).
    """
    if recall_count > 0:
        print(
            "Warning: finish-message fallback used after dispatch recall "
            f"(dispatch_recall_count={recall_count}) — no text answer was "
            "captured; check the agent logs for a capture regression",
            flush=True,
        )


def _posted_recall_count(state: WorkflowState | None) -> int:
    """``dispatch_recall_count`` of the run's latest history entry, if any."""
    if state is None or not state.history:
        return 0
    metrics = state.history[-1].metrics
    return metrics.dispatch_recall_count if metrics is not None else 0


# ── Core cleaning functions ───────────────────────────────────────


def clean_message(raw: str) -> str:
    """Strip internal XML/HTML tags from an agent message.

    Removes AGENT_RUN_CONFIG sentinels (plus legacy <agentRunConfig> blocks).

    All regex operations use re.DOTALL to handle multi-line content safely.

    Args:
        raw: The raw agent final message text.

    Returns:
        Cleaned message text without any internal tags.
    """
    content = raw.strip()

    # Strip AGENT_RUN_CONFIG sentinels (canonical, single-line HTML comments)
    # plus any legacy <agentRunConfig> blocks (hygiene only) — they contain
    # JSON with special characters (<, >, {, }, $) that break bash echo.
    content = _LEGACY_AGENT_RUN_CONFIG.sub("", content)
    content = strip_agent_run_config(content)

    # Clean up residual blank lines from removals (collapse 3+ newlines to 2).
    content = re.sub(r"\n{3,}", "\n\n", content).strip()

    return content


# ── Fallback: parse console.txt ───────────────────────────────────


def extract_from_console_log() -> str:
    """Parse console.txt to extract agent message as a fallback.

    File-driven wrapper around :func:`console_log_fallback` (the shared body);
    kept as the legacy entry point for callers that read the working directory.

    Returns:
        Extracted message text from console.txt.
    """
    if not os.path.isfile("console.txt") or os.path.getsize("console.txt") == 0:
        return ""

    with open("console.txt", "r", encoding="utf-8") as f:
        log_content = f.read()

    return console_log_fallback(log_content)


def console_log_fallback(log_content: str) -> str:
    """Extract an agent message out of console output.

    Looks for "Message from Agent" blocks in the log output.
    Falls back to the last 50 meaningful lines if no structured block found.

    Args:
        log_content: Raw console output.

    Returns:
        Extracted message text from the console output.
    """
    # Try to find the last "Message from Agent" block
    blocks = log_content.split(_CONSOLE_MARKER)

    if len(blocks) > 1:
        last_message = blocks[-1].split("Tokens:")[0].strip()
        last_message = re.sub(
            r'\{"asctime":.*"message": "Task completed successfully"}',
            "",
            last_message,
        ).strip()
    else:
        # Fallback: take last 50 meaningful lines
        lines = log_content.strip().split("\n")
        meaningful_lines = [l for l in lines if not re.match(r"^\s*\{", l)]
        last_message = (
            "\n".join(meaningful_lines[-50:])
            if meaningful_lines
            else "\n".join(lines[-50:])
        )

    # Truncate at paragraph boundary if needed
    last_message = _truncate_at_boundary(last_message)

    return last_message


# ── Helpers ───────────────────────────────────────────────────────


def _truncate_at_boundary(text: str, max_chars: int = MAX_COMMENT_LENGTH) -> str:
    """Truncate text at ``max_chars``, preferring paragraph boundaries.

    Args:
        text: The text to potentially truncate.
        max_chars: The character budget; defaults to the comment cap. A
            two-argument form (issue #913) lets callers bound the *console*
            fallback independently of the answer body, which is no longer
            truncated here — it is split across comments at publish time.

    Returns:
        Truncated text with "...*(comment truncated due to size limit)*" appended.
    """
    if len(text) <= max_chars:
        return text

    text = text[:max_chars]
    last_break = text.rfind("\n\n")
    if last_break > max_chars * 0.5:
        text = text[:last_break]
    return text + "\n\n...*(comment truncated due to size limit)*"


def escape_agent_triggers(content: str) -> str:
    """Neutralize @@agent trigger tokens so a posted comment can't re-trigger
    the issue_comment workflow gate (issue #482).

    Inserts a zero-width space (U+200B) between @@ and the agent name
    (e.g. "@@architect" -> "@@\u200barchitect"). This is invisible when rendered but
    breaks the contains(body, '@@architect') substring match in the workflow
    trigger condition.
    """
    return _AGENT_TRIGGER.sub(lambda m: m.group(0).replace("@@", "@@\u200b", 1), content)


def neutralize_human_pings(content: str) -> str:
    """Make bare human @mentions inert in agent-authored comment bodies (#795).

    Notification is the engine's responsibility: an agent reports its result and
    never pings a human. Inserting a zero-width space (U+200B) between the ``@``
    and the login keeps the text readable but stops GitHub from
    linkifying/notifying, and keeps it invisible to the loop guard's human-ping
    detector. The ``@@agent`` token form is deliberately left to
    :func:`escape_agent_triggers`, which owns that transformation.

    Only agent-authored bodies pass through here; the engine's own notices and
    its crash-path ``@author`` pings are exempt (they are built outside the
    agent-body finalizer). Fenced blocks and inline code spans are emitted
    verbatim — a mention there never notifies, so de-fanging it would only
    mangle the snippet.
    """

    def _emitted(match: re.Match[str]) -> str:
        if match.group("code") is not None:
            return match.group("code")
        return "@\u200b" + match.group("login")

    return _HUMAN_MENTION.sub(_emitted, content)


def _apply_context_collapse(cleaned: str, role_name: str | None = None) -> str:
    """Wrap context-report sections in <details> blocks (issue #557).

    Only applied when the role is "context": the title + executive summary
    (above the first ### heading) stay visible; FILES FOUND / RELEVANT
    SNIPPETS / EXTERNAL REFERENCES / OTHER FILES collapse by default.
    Non-context roles and bodies without any recognized heading pass through
    unchanged.

    Args:
        cleaned: The cleaned comment body.
        role_name: Explicit role (engine seam, issue #590); ``None`` reads
            ``ROLE_NAME`` from the environment, which is the GHA path.
    """
    role = os.environ.get("ROLE_NAME", "") if role_name is None else role_name
    if role != "context":
        return cleaned
    collapsed = collapse_sections(cleaned, CONTEXT_SECTION_HEADINGS)
    return cleaned if collapsed == cleaned else collapsed


def _write_output(content: str, poster: Poster | None = None) -> None:
    """Publish content, either through the injected poster or the GHA path.

    Content is passed through escape_agent_triggers() so no comment posted
    from any code path can contain a raw @@ trigger token. When a ``poster``
    is injected (engine seam, issue #590) the body goes through it instead of
    the peter-evans/comment hand-off file.

    Args:
        content: The comment body text to publish.
        poster: Optional injected poster callable (engine seam, issue #590).
    """
    content = escape_agent_triggers(content)

    if poster is not None:
        poster(content)
        return

    with open("github_comment.md", "w", encoding="utf-8") as f:
        f.write(content)

    github_env = os.environ.get("GITHUB_ENV", "")
    if github_env:
        with open(github_env, "a") as ge:
            ge.write("HAS_COMMENT=true\n")


# ── Main entry point ──────────────────────────────────────────────


# Fallback bodies for the two crash paths (Path C / Path D). Kept as module
# constants so the engine (issue #590) posts the exact same text as the GHA path.
_CRASH_MESSAGE = (
    "> \u26a0\ufe0f The agent crashed or timed out before producing output. "
    "Please check the full logs in the workflow run."
)
_NO_FINAL_MESSAGE = (
    "> \u26a0\ufe0f The agent did not produce a final message. "
    "Please check the full logs in the workflow run."
)


# ── Answer-comment header (issue #736) ───────────────────────────
#
# The run's WorkflowState belongs on the agent's *answer* comment: it is the
# run's authoritative output and its ``dispatch`` drives routing. These
# constants/helpers restore the legacy "Add bot marker and workflow state to
# comment" step inside the shared body builder, so both hosts frame the exact
# same comment.

# The bot marker MUST stay on line 1 (issue #478) so bot detection is exact.
BOT_MARKER = "<!-- bot-workflow-terminal -->"

# Deterministic continuation token (issue #478): the next CI run fires on this
# hidden marker rather than on stray ``@@`` tokens in agent prose.
CONTINUE_TOKEN = "<!-- workflow-continue -->\n"

# Dispatch statuses that keep the flow alive and therefore carry the token.
# ``soft_kill`` (issue #793) belongs here even though it is a stop: the
# engine's own soft-kill conclusion comment must re-trigger the run so the
# next run's ``resolve_soft_kill_route`` can retry the interrupted step in
# place, bounded by ``MAX_SOFT_RETRIES`` (then halt for a human).
_LIVE_STATUSES = ("advance", "loop", "soft_kill")

# Role label for the answer header's announcement line (legacy mapping).
_ROLE_DISPLAY_NAMES = {
    "architect": "Architect",
    "context": "Context",
    "dev": "Developer",
    "review": "Reviewer",
    "po": "Product Owner",
}


def _display_name_for(role: str) -> str:
    """Human-readable role label for the answer header (legacy mapping)."""
    return _ROLE_DISPLAY_NAMES.get(role, role.capitalize())


def _continue_token(state: WorkflowState) -> str:
    """The continue token for live dispatches; empty for hold/terminate."""
    status = state.dispatch.status if state.dispatch else "advance"
    return CONTINUE_TOKEN if status in _LIVE_STATUSES else ""


def format_answer_header(role_name: str | None, state: WorkflowState | None) -> str:
    """Build the marker/token/state header prepended to the answer (issue #736).

    Public so the engine publish seam can place this header on the first of
    several answer comments while the body is split across them (issue #913).
    A run that emitted no state still gets the bot marker, caller marker and
    continue token (matching the legacy step when the file was absent); only the
    collapsed state section is omitted.
    """
    role = (role_name or "").strip() or "unknown"
    token = CONTINUE_TOKEN if state is None else _continue_token(state)
    section = "" if state is None else format_collapsed_section(state)
    return (
        f"{BOT_MARKER}\n"
        f"<!-- bot-agent-{role} -->\n"
        f"{token}"
        f"{section}"
        f"\n*{_display_name_for(role)} answer*\n\n"
    )


# Backwards-compatible alias; the private name predates the public export.
_format_answer_header = format_answer_header


def _select_fallback_body(
    *,
    latest_answer: str,
    final_message: str,
    console_log: str = "",
    has_final_message: bool | None = None,
    has_console_log: bool | None = None,
    trigger_author: str = "",
    role_name: str | None = None,
    recall_count: int = 0,
    engine_sentinel: str = "",
) -> str:
    """The four-path publish fallback chain over explicit inputs.

    Backs :func:`build_comment_body`: a crashed or empty agent run posts the
    same structured fallback message on either host instead of the engine
    forking a subset of the semantics (issue #590).

    Args:
        latest_answer: Raw ``latest_answer.md`` content (the preferred body).
        final_message: Raw ``final_message.txt`` content.
        console_log: Raw ``console.txt`` content.
        has_final_message: Whether the ``final_message.txt`` artifact existed
            with content. ``None`` derives it from ``final_message``.
        has_console_log: Whether the ``console.txt`` artifact existed with
            content. ``None`` derives it from ``console_log``.
        trigger_author: Author pinged on the crash paths (``TRIGGER_AUTHOR``).
        role_name: Role for the context collapse; ``None`` reads the environment.
        recall_count: The run's ``dispatch_recall_count`` (from the workflow
            state); flags post-recall finish-fallbacks in CI logs (issue #558).
        engine_sentinel: The engine-rendered hand-off sentinel
            (:func:`render_handoff_sentinel`). When set it is authoritative;
            the legacy prose scrape (:func:`merge_handoff_sentinels`) only runs
            as a fallback (issue #830).

    Returns:
        The cleaned body, without the answer-comment header.
    """
    if has_final_message is None:
        has_final_message = bool(final_message)
    if has_console_log is None:
        has_console_log = bool(console_log)
    author_ping = f"@{trigger_author} " if trigger_author else ""

    def console_fallback() -> str:
        if not has_console_log:
            return ""
        return neutralize_human_pings(console_log_fallback(console_log))

    def finalized(cleaned: str) -> str:
        # No length truncation here (issue #913): a long answer is split across
        # comments at publish time, so truncating first would discard exactly
        # the content the split exists to preserve. The context <details>
        # collapse (#557) and ping neutralization (#795) still apply.
        cleaned = neutralize_human_pings(cleaned)
        return _apply_context_collapse(cleaned, role_name)

    # ── Path A: Structured final message ────────────────────────
    if has_final_message:
        raw = final_message.strip()

        # Sentinel: agent ran but produced no final message
        if raw.startswith("__NO_FINAL_MESSAGE__"):
            print("Warning: No final message from agent (sentinel detected), falling back to console.txt",
                  flush=True)
            fallback = console_fallback()
            if fallback:
                return fallback
            return author_ping + _CRASH_MESSAGE

        # ── Latest-answer ETL (issue #522) ───────────────────────
        # The agent's latest full answer is the preferred posted body; the
        # finish message (raw) is fallback only. Both sources feed the
        # hand-off sentinel merge so dispatch/branching always reach the
        # comment.
        latest_raw = latest_answer
        if latest_raw.strip():
            body_raw = select_posted_body(latest_raw, raw)
            if body_raw.strip():
                cleaned = clean_message(body_raw)
                if cleaned:
                    # The engine's typed sentinel wins; only when it is absent
                    # (legacy/agent-authored runs) do we scrape the prose.
                    merged_sentinel = engine_sentinel or merge_handoff_sentinels(latest_raw, raw)
                    if merged_sentinel:
                        cleaned += "\n\n" + merged_sentinel
                    print(f"Using latest answer ({len(cleaned)} characters)", flush=True)
                    return finalized(cleaned)

        # Normal path: clean, write
        cleaned = clean_message(raw)

        if cleaned:
            # Latest-answer artifact empty/missing → finish message is the
            # posted body: flag post-recall fallbacks for visibility (#558).
            _warn_post_recall_fallback(recall_count)
            if engine_sentinel:
                cleaned += "\n\n" + engine_sentinel
            print(f"Using final_message.txt ({len(cleaned)} characters)", flush=True)
            return finalized(cleaned)

        # Content was empty after cleaning — fall through to console.txt
        print("Warning: final_message.txt has no meaningful content after cleaning, "
              "falling back to console.txt", flush=True)
        fallback = console_fallback()
        if fallback:
            return fallback
        return author_ping + _CRASH_MESSAGE

    # ── Path B: console.txt only (final message missing/empty) ──
    fallback = console_fallback()
    if fallback:
        return fallback

    # ── Path C/D: No output at all ──────────────────────────────
    return author_ping + _NO_FINAL_MESSAGE


def build_comment_body(
    *,
    latest_answer: str,
    final_message: str,
    console_log: str = "",
    has_final_message: bool | None = None,
    has_console_log: bool | None = None,
    trigger_author: str = "",
    role_name: str | None = None,
    state: WorkflowState | None = None,
) -> str:
    """The posted-comment body: answer-comment header + fallback body.

    Single source of truth for the posted comment: :func:`prepare_comment` (GHA)
    and ``nebuko.engine.run_loop.Engine.publish`` both call this, so the same comment
    is framed on either host (issue #590). The workflow-state header (issue
    #736) is prepended here because it frames *whatever* the run produced —
    latest answer, final message, console fallback or crash notice — exactly as
    the legacy "Add bot marker and workflow state to comment" step did.

    Args:
        latest_answer: Raw ``latest_answer.md`` content (the preferred body).
        final_message: Raw ``final_message.txt`` content.
        console_log: Raw ``console.txt`` content.
        has_final_message: Whether the ``final_message.txt`` artifact existed
            with content. ``None`` derives it from ``final_message``.
        has_console_log: Whether the ``console.txt`` artifact existed with
            content. ``None`` derives it from ``console_log``.
        trigger_author: Author pinged on the crash paths (``TRIGGER_AUTHOR``).
        role_name: Answer-comment role; also drives the context body collapse.
            ``None`` reads ``ROLE_NAME`` from the environment (GHA path).
        state: The agent's ``workflow_state.json`` artifact, or ``None`` when
            the run emitted none. ``None`` still emits the marker/token header
            but omits the collapsed state section.

    Returns:
        The framed body to escape and publish.
    """
    return _format_answer_header(role_name, state) + _select_fallback_body(
        latest_answer=latest_answer,
        final_message=final_message,
        console_log=console_log,
        has_final_message=has_final_message,
        has_console_log=has_console_log,
        trigger_author=trigger_author,
        role_name=role_name,
        recall_count=_posted_recall_count(state),
        engine_sentinel=render_handoff_sentinel(state),
    )


def _read_workflow_state() -> WorkflowState | None:
    """Load the agent's ``workflow_state.json`` artifact, when present.

    Graceful degradation mirrors the legacy step: a missing, empty or malformed
    file yields ``None`` so the answer still posts its marker/token header
    without a state section (issue #736).
    """
    if not _artifact_has_content(WORKFLOW_STATE_FILE):
        return None
    try:
        from workflow_state.workflow_state import WorkflowState

        return WorkflowState.model_validate(json.loads(_read_artifact(WORKFLOW_STATE_FILE)))
    except Exception as exc:  # noqa: BLE001 - a bad artifact must not block posting
        print(f"Warning: could not parse {WORKFLOW_STATE_FILE}: {exc}", flush=True)
        return None


def prepare_comment(poster: Poster | None = None) -> bool:
    """Orchestrate comment preparation from final_message.txt or fallbacks.

    File-driven wrapper around :func:`build_comment_body`; mirrors the
    four-path logic from the workflow YAML:
      Path A: Use final_message.txt (strip internal tags, write output)
      Path B: Parse console.txt for "Message from Agent" blocks
      Path C: Agent crashed before producing output
      Path D: final_message.txt doesn't exist

    Args:
        poster: Optional injected poster callable (engine seam, issue #590).
            ``None`` keeps the current default path. ``select_posted_body``
            remains untouched.

    Returns:
        True if github_comment.md was written successfully.
    """
    has_final_message = _artifact_has_content("final_message.txt")
    has_console_log = _artifact_has_content("console.txt")
    body = build_comment_body(
        latest_answer=_read_latest_answer(),
        final_message=_read_artifact("final_message.txt") if has_final_message else "",
        console_log=_read_artifact("console.txt") if has_console_log else "",
        has_final_message=has_final_message,
        has_console_log=has_console_log,
        trigger_author=os.environ.get("TRIGGER_AUTHOR", ""),
        role_name=os.environ.get("ROLE_NAME", ""),
        state=_read_workflow_state(),
    )
    _write_output(body, poster=poster)
    return True


def _artifact_has_content(path: str) -> bool:
    """The legacy "exists and is not zero bytes" artifact test."""
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _read_artifact(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


if __name__ == "__main__":
    success = prepare_comment()
    sys.exit(0 if success else 1)
