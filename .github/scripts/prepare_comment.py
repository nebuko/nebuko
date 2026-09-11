"""Prepare github_comment.md from final_message.txt.

Reads the agent's final message, strips internal XML/HTML tags
(AGENT_RUN_CONFIG sentinels + legacy <agentRunConfig>, splitPlan,
agent-metrics), and writes the cleaned
content to github_comment.md for the peter-evans/comment action.

All regex operations use re.DOTALL for robust multi-line matching.
No bash intermediate — Python reads and writes files directly.

Handles multiple paths mirroring the workflow logic:
  Path A: Structured final_message.txt (happy path)
  Path B: Fallback to console.log parsing
  Path C: Agent crashed before producing output
  Path D: final_message.txt doesn't exist
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_output import AGENT_RUN_CONFIG_PATTERN, strip_agent_run_config
from workflow_state.features.comment.to_git_comment import collapse_sections

# ── Regex patterns (all DOTALL for multi-line safety) ─────────────

# agent-metrics HTML comment — may span multiple lines
_METRICS_COMMENT = re.compile(r"<!--\s*agent-metrics:.*?-->", re.DOTALL)

# Legacy <agentRunConfig> XML block — comment hygiene ONLY.
# The legacy form is no longer a dispatch-parsing path (issue #450);
# this strip prevents stale raw XML from leaking into rendered comments.
_LEGACY_AGENT_RUN_CONFIG = re.compile(r"<agentRunConfig>.*?</agentRunConfig>", re.DOTALL)

# <splitPlan> XML block — safety net (already replaced upstream)
_SPLIT_PLAN = re.compile(r"<splitPlan>.*?</splitPlan>", re.DOTALL)

# Console.log "Message from Agent" separator
_CONSOLE_MARKER = "Message from Agent " + "\u2500" * 63

# @@agent trigger tokens (e.g. @@genius, @@dev)
_AGENT_TRIGGER = re.compile(r"(?<![\w])@@([A-Za-z0-9_-]+)")

# dispatch_recall_count inside the agent-metrics JSON payload (issue #558):
# surfaces the post-recall finish-fallback case as a WARNING in CI logs.
_RECALL_COUNT_IN_METRICS = re.compile(r'"dispatch_recall_count"\s*:\s*(\d+)')

MAX_COMMENT_LENGTH = 60000

# Section headings of the ctx report template (.agents/prompts/ctx.md).
# Wrapped in <details> at posting time so a long ctx report stays scannable
# while remaining fully auditable in the comment body (issue #557).
CTX_SECTION_HEADINGS = [
    "FILES FOUND",
    "RELEVANT SNIPPETS",
    "EXTERNAL REFERENCES",
    "OTHER FILES",
]

# Artifact written by the capture layer in build_output.py (issue #522):
# the agent's latest full answer, preferred over the finish message as the
# posted-comment body.
LATEST_ANSWER_FILE = "latest_answer.md"


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


def _read_latest_answer(path: str = LATEST_ANSWER_FILE) -> str:
    """Read the latest-answer artifact; return "" if missing/unreadable."""
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _warn_post_recall_fallback(metrics_comment: str) -> None:
    """Surface the post-recall finish-fallback case in CI logs (issue #558).

    Fires when the posted body came from the finish message although the
    run went through a dispatch-recall pass — a signature of the
    clobber/fallback family of bugs. Reads the count from the
    agent-metrics payload already attached to the body; adds no new
    plumbing channel.
    """
    if not metrics_comment:
        return
    match = _RECALL_COUNT_IN_METRICS.search(metrics_comment)
    if match and int(match.group(1)) > 0:
        print(
            "Warning: finish-message fallback used after dispatch recall "
            f"(dispatch_recall_count={match.group(1)}) — no text answer was "
            "captured; check the agent logs for a capture regression",
            flush=True,
        )


# ── Core cleaning functions ───────────────────────────────────────


def extract_metrics(content: str) -> str:
    """Extract the agent-metrics HTML comment from content, if present.

    Returns the full <!-- agent-metrics: ... --> string, or empty string.
    """
    match = _METRICS_COMMENT.search(content)
    return match.group(0) if match else ""


def clean_message(raw: str, metrics_comment: str) -> str:
    """Strip internal XML/HTML tags from an agent message.

    Removes AGENT_RUN_CONFIG sentinels (plus legacy <agentRunConfig> blocks),
    <splitPlan>, and <!-- agent-metrics --> blocks.
    The metrics comment is passed separately so it can be re-appended
    at the end (preserving its position from the original workflow logic).

    All regex operations use re.DOTALL to handle multi-line content safely.

    Args:
        raw: The raw agent final message text.
        metrics_comment: The extracted metrics comment (preserved separately).

    Returns:
        Cleaned message text without any internal tags.
    """
    content = raw.strip()

    # Strip AGENT_RUN_CONFIG sentinels (canonical, single-line HTML comments)
    # plus any legacy <agentRunConfig> blocks (hygiene only) — they contain
    # JSON with special characters (<, >, {, }, $) that break bash echo.
    content = _LEGACY_AGENT_RUN_CONFIG.sub("", content)
    content = strip_agent_run_config(content)

    # Strip <splitPlan> blocks (already replaced with summary upstream).
    content = _SPLIT_PLAN.sub("", content)

    # Strip <!-- agent-metrics --> comments (will be re-added at end).
    content = _METRICS_COMMENT.sub("", content)

    # Clean up residual blank lines from removals (collapse 3+ newlines to 2).
    content = re.sub(r"\n{3,}", "\n\n", content).strip()

    # Re-add metrics comment at the end if one was preserved.
    if metrics_comment:
        content += "\n\n" + metrics_comment

    return content


# ── Fallback: parse console.log ───────────────────────────────────


def extract_from_console_log(metrics_comment: str) -> str:
    """Parse console.log to extract agent message as a fallback.

    Looks for "Message from Agent" blocks in the log output.
    Falls back to the last 50 meaningful lines if no structured block found.

    Args:
        metrics_comment: Preserved metrics comment to append if available.

    Returns:
        Extracted message text from console.log.
    """
    if not os.path.isfile("console.log") or os.path.getsize("console.log") == 0:
        return ""

    with open("console.log", "r", encoding="utf-8") as f:
        log_content = f.read()

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

    if metrics_comment:
        last_message += "\n\n" + metrics_comment

    return last_message


# ── Helpers ───────────────────────────────────────────────────────


def _truncate_at_boundary(text: str) -> str:
    """Truncate text at MAX_COMMENT_LENGTH, preferring paragraph boundaries.

    Args:
        text: The text to potentially truncate.

    Returns:
        Truncated text with "...*(comment truncated due to size limit)*" appended.
    """
    if len(text) <= MAX_COMMENT_LENGTH:
        return text

    text = text[:MAX_COMMENT_LENGTH]
    last_break = text.rfind("\n\n")
    if last_break > MAX_COMMENT_LENGTH * 0.5:
        text = text[:last_break]
    return text + "\n\n...*(comment truncated due to size limit)*"


def escape_agent_triggers(content: str) -> str:
    """Neutralize @@agent trigger tokens so a posted comment can't re-trigger
    the issue_comment workflow gate (issue #482).

    Inserts a zero-width space (U+200B) between @@ and the agent name
    (e.g. "@@genius" -> "@@\u200bgenius"). This is invisible when rendered but
    breaks the contains(body, '@@genius') substring match in the workflow
    trigger condition.
    """
    return _AGENT_TRIGGER.sub(lambda m: m.group(0).replace("@@", "@@\u200b", 1), content)


def _apply_ctx_collapse(cleaned: str) -> str:
    """Wrap ctx-report sections in <details> blocks (issue #557).

    Only applied when ROLE_NAME is "ctx": the title + executive summary
    (above the first ### heading) stay visible; FILES FOUND / RELEVANT
    SNIPPETS / EXTERNAL REFERENCES / OTHER FILES collapse by default.
    Non-ctx roles and bodies without any recognized heading pass through
    unchanged.
    """
    if os.environ.get("ROLE_NAME", "") != "ctx":
        return cleaned
    collapsed = collapse_sections(cleaned, CTX_SECTION_HEADINGS)
    return cleaned if collapsed == cleaned else collapsed


def _write_output(content: str) -> None:
    """Write content to github_comment.md and set HAS_COMMENT env var.

    Content is passed through escape_agent_triggers() so no comment posted
    from any code path can contain a raw @@ trigger token.

    Args:
        content: The comment body text to write.
    """
    content = escape_agent_triggers(content)
    with open("github_comment.md", "w", encoding="utf-8") as f:
        f.write(content)

    github_env = os.environ.get("GITHUB_ENV", "")
    if github_env:
        with open(github_env, "a") as ge:
            ge.write("HAS_COMMENT=true\n")


# ── Main entry point ──────────────────────────────────────────────


def prepare_comment() -> bool:
    """Orchestrate comment preparation from final_message.txt or fallbacks.

    Mirrors the four-path logic from the workflow YAML:
      Path A: Use final_message.txt (strip internal tags, write output)
      Path B: Parse console.log for "Message from Agent" blocks
      Path C: Agent crashed before producing output
      Path D: final_message.txt doesn't exist

    Returns:
        True if github_comment.md was written successfully.
    """
    trigger_author = os.environ.get("TRIGGER_AUTHOR", "")
    author_ping = f"@{trigger_author} " if trigger_author else ""

    # ── Path A: Structured final_message.txt ────────────────────
    if os.path.isfile("final_message.txt") and os.path.getsize("final_message.txt") > 0:
        with open("final_message.txt", "r", encoding="utf-8") as f:
            raw = f.read().strip()

        # Sentinel: agent ran but produced no final message
        if raw.startswith("__NO_FINAL_MESSAGE__"):
            print("Warning: No final message from agent (sentinel detected), falling back to console.log",
                  flush=True)
            metrics_comment = extract_metrics(raw)
            fallback = extract_from_console_log(metrics_comment)
            if fallback:
                _write_output(fallback)
                return True
            error_msg = (
                author_ping
                + "> \u26a0\ufe0f The agent crashed or timed out before producing output. "
                + "Please check the full logs in the workflow run."
            )
            if metrics_comment:
                error_msg += "\n\n" + metrics_comment
            _write_output(error_msg)
            return True

        # ── Latest-answer ETL (issue #522) ───────────────────────
        # The agent's latest full answer is the preferred posted body;
        # the finish message (raw) is fallback only. Both sources feed
        # the hand-off sentinel merge so dispatch/branching always
        # reach the comment.
        latest_raw = _read_latest_answer()
        if latest_raw.strip():
            body_raw = select_posted_body(latest_raw, raw)
            if body_raw.strip():
                body_metrics = extract_metrics(body_raw) or extract_metrics(raw)
                cleaned = clean_message(body_raw, "")
                if cleaned:
                    merged_sentinel = merge_handoff_sentinels(latest_raw, raw)
                    if merged_sentinel:
                        cleaned += "\n\n" + merged_sentinel
                    if body_metrics:
                        cleaned += "\n\n" + body_metrics
                    print(f"Using latest answer ({len(cleaned)} characters)", flush=True)
                    cleaned = _apply_ctx_collapse(cleaned)
                    cleaned = _truncate_at_boundary(cleaned)
                    _write_output(cleaned)
                    return True

        # Normal path: extract metrics, clean, write
        metrics_comment = extract_metrics(raw)
        cleaned = clean_message(raw, metrics_comment)

        if cleaned:
            # Latest-answer artifact empty/missing → finish message is the
            # posted body: flag post-recall fallbacks for visibility (#558).
            _warn_post_recall_fallback(metrics_comment)
            print(f"Using final_message.txt ({len(cleaned)} characters)", flush=True)
            cleaned = _apply_ctx_collapse(cleaned)
            cleaned = _truncate_at_boundary(cleaned)
            _write_output(cleaned)
            return True

        # Content was empty after cleaning — fall through to console.log
        print("Warning: final_message.txt has no meaningful content after cleaning, "
              "falling back to console.log", flush=True)
        fallback = extract_from_console_log(metrics_comment)
        if fallback:
            _write_output(fallback)
            return True

        # Console.log also empty — crash message
        error_msg = (
            author_ping
            + "> \u26a0\ufe0f The agent crashed or timed out before producing output. "
            + "Please check the full logs in the workflow run."
        )
        if metrics_comment:
            error_msg += "\n\n" + metrics_comment
        _write_output(error_msg)
        return True

    # ── Path B: console.log only (final_message.txt missing/empty) ─
    metrics_comment = ""
    fallback = extract_from_console_log(metrics_comment)
    if fallback:
        _write_output(fallback)
        return True

    # ── Path C/D: No output at all ──────────────────────────────
    error_msg = (
        author_ping
        + "> \u26a0\ufe0f The agent did not produce a final message. "
        + "Please check the full logs in the workflow run."
    )
    _write_output(error_msg)
    return True


if __name__ == "__main__":
    success = prepare_comment()
    sys.exit(0 if success else 1)
