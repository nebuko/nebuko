import re

from ...workflow_state import WorkflowState, STATE_MARKER_START, STATE_MARKER_END
from ..to_json.to_json import state_to_json

def format_collapsed_section(state: WorkflowState) -> str:
    """
    Format the WorkflowState as a GitHub-flavored Markdown collapsed section.

    Output:
    <details>
    <summary>{logo} Workflow State (run {run_id})</summary>

    ```json
    { ... }
    ```

    </details>

    The state markers (WORKFLOW_STATE_START/END) are embedded inside the
    code block for reliable extraction by scripts. ``{logo}`` is the
    theme-adaptive brand mark (issue #778).

    Args:
        state: The WorkflowState to format.

    Returns:
        Markdown string ready to prepend to a comment body.
    """
    from config import brand

    json_str = state_to_json(state, compact=False)
    summary_label = f"{brand().logo_mark()} Workflow State (run {state.run_id})"

    lines = [
        "<details>",
        f"<summary>{summary_label}</summary>",
        "",
        "```json",
        STATE_MARKER_START,
        json_str,
        STATE_MARKER_END,
        "```",
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines)

def wrap_comment_with_state(state: WorkflowState, body: str) -> str:
    """
    Prepend the collapsed state section to a comment body.

    Args:
        state: The WorkflowState to embed.
        body: The original comment body (human-readable content).

    Returns:
        Complete comment string with state section at the top.
    """
    section = format_collapsed_section(state)
    return section + body

def _strip_md_emphasis(line: str) -> str:
    """Strip surrounding * / _ emphasis markers from a line (up to two pairs)."""
    stripped = line.strip()
    for _ in range(2):
        for quote in ("*", "_"):
            if len(stripped) >= 2 and stripped.startswith(quote) and stripped.endswith(quote):
                inner = stripped[1:-1].strip()
                if inner:
                    stripped = inner
    return stripped


def _section_end_index(lines: list[str], start: int) -> int:
    """Index of the first line >= start that opens a same-or-higher-level heading.

    Returns len(lines) when no such heading exists (section runs to EOF).
    Headings already inside a fenced code block are ignored.
    """
    level = len(re.match(r"#+", lines[start]).group(0))
    in_fence = False
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"(#{1,6})\s", line)
        if match and len(match.group(1)) <= level:
            return i
    return len(lines)


def _resume_line(lines: list[str], start: int, end: int) -> str:
    """First italic one-liner right after the heading (skipping blanks), de-emphasized."""
    for line in lines[start + 1:end]:
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"[*_].+[*_]", stripped):
            return _strip_md_emphasis(stripped)
        break
    return ""


def collapse_sections(body: str, section_headings: list[str]) -> str:
    """Wrap each recognized top-level section in <details><summary>.

    For each heading in ``section_headings``, find the first markdown heading
    whose title text matches (case-insensitive), and wrap everything from that
    line until the next same-or-higher-level heading (or EOF) in a
    <details> block. The <summary> carries the heading text plus, when the
    line immediately after the heading is an italic one-liner (the template's
    per-section resume), that resume with its emphasis markers removed.

    Content inside each <details> is byte-identical to the input — only
    wrapped — so downstream consumers reading raw comment bodies are
    unaffected. Headings that don't match, or empty lists, leave the body
    unchanged (graceful degradation, no data loss).
    """
    if not body or not section_headings:
        return body

    wanted = {h.strip().lower() for h in section_headings}
    lines = body.split("\n")

    boundaries: list[int] = []
    for i, line in enumerate(lines):
        match = re.match(r"(#{1,6})\s+(.*)", line)
        if match and match.group(2).strip().lower() in wanted:
            boundaries.append(i)
    if not boundaries:
        return body

    result_lines = list(lines[:boundaries[0]])
    for start in boundaries:
        end = _section_end_index(lines, start)
        heading_text = re.match(r"(#{1,6})\s+(.*)", lines[start]).group(2).strip()
        resume = _resume_line(lines, start, end)
        summary = f"{heading_text} — {resume}" if resume else heading_text
        result_lines.extend(["<details>", f"<summary>{summary}</summary>", ""])
        result_lines.extend(lines[start:end])
        result_lines.extend(["</details>", ""])

    # Drop a single trailing blank introduced by the last closing </details>,
    # mirroring the pre-existing trailing-newline shape of the input.
    if result_lines and result_lines[-1] == "" and len(result_lines) > 1:
        result_lines.pop()
    return "\n".join(result_lines)


# A run of characters that stays inside one ``<details>`` element: the tempered
# dot refuses to cross the element's own closing tag, so a marker-bearing block
# is matched as a whole without ever swallowing a neighbouring block.
_INSIDE_DETAILS = r"(?:(?!</details>).)*?"

_MARKER_STATE_BLOCK_RE = re.compile(
    r"<details>"
    + _INSIDE_DETAILS
    + re.escape(STATE_MARKER_START)
    + _INSIDE_DETAILS
    + re.escape(STATE_MARKER_END)
    + _INSIDE_DETAILS
    + r"</details>\s*",
    flags=re.DOTALL,
)

_LABEL_STATE_BLOCK_RE = re.compile(
    r"<details>\s*<summary>(?:🤖|<picture>.*?</picture>|<img[^>]*>)?\s*Workflow State.*?</details>\s*",
    flags=re.DOTALL,
)

_BOT_MARKER_LINE_RE = re.compile(
    r"(?m)^[ \t]*<!--[ \t]*"
    r"(?:bot-workflow-terminal|bot-agent-[A-Za-z0-9\-]+|bot-human-action|workflow-continue)"
    r"[ \t]*-->[ \t]*\n?"
)


def strip_state_section(body: str) -> str:
    """Remove workflow state and bot routing noise from a comment body.

    Returns just the human-readable content. Removal is content-anchored, so it
    survives re-branding and state-version changes (issue #844):

    1. The collapsed ``<details>`` block carrying the
       ``WORKFLOW_STATE_START``/``WORKFLOW_STATE_END`` markers — the canonical,
       version-proof anchor. The block is matched without crossing its own
       closing tag, so unrelated ``<details>`` blocks are never swallowed.
    2. Fallback for legacy comments whose state block predates the markers: the
       block whose ``<summary>`` label ends in ``Workflow State``. Three prefix
       generations stay tolerated (issue #778): the legacy '🤖' emoji, the
       theme-adaptive ``<picture>`` logo mark, and a bare label.
    3. Standalone bot routing marker lines (``bot-workflow-terminal``,
       ``bot-agent-*``, ``workflow-continue``).

    Any other content is preserved byte-for-byte (modulo the trailing strip).
    """
    cleaned = _MARKER_STATE_BLOCK_RE.sub("", body)
    cleaned = _LABEL_STATE_BLOCK_RE.sub("", cleaned)
    cleaned = _BOT_MARKER_LINE_RE.sub("", cleaned)
    return cleaned.strip()
