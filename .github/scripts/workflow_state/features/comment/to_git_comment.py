import re

from ...workflow_state import WorkflowState, to_json, STATE_MARKER_START, STATE_MARKER_END

def format_collapsed_section(state: WorkflowState) -> str:
    """
    Format the WorkflowState as a GitHub-flavored Markdown collapsed section.

    Output:
    <details>
    <summary>🤖 Workflow State (v{version})</summary>

    ```json
    { ... }
    ```

    </details>

    The state markers (WORKFLOW_STATE_START/END) are embedded inside the
    code block for reliable extraction by scripts.

    Args:
        state: The WorkflowState to format.

    Returns:
        Markdown string ready to prepend to a comment body.
    """
    json_str = to_json(state, compact=False)
    summary_label = f"🤖 Workflow State (v{state.version})"

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

def strip_state_section(body: str) -> str:
    """Remove the workflow state collapsed section from a comment body, returning just the human-readable content.
    
    Only targets the specific <details> block with the '🤖 Workflow State' summary label,
    preserving any other legitimate <details> blocks in the content.
    """
    cleaned = re.sub(
        r"<details>\s*<summary>🤖 Workflow State.*?</details>\s*",
        "",
        body,
        flags=re.DOTALL,
    )
    return cleaned.strip()
