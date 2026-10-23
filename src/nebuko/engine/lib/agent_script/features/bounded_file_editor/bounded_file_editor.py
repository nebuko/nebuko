"""A ``file_editor`` replacement that keeps unbounded views off the context.

The GLM-5.3-flash run (issue #1114 console log) paid ~16K characters of
context for one whole-file ``view`` of a 1,322-line test file — and did it
more than once. The SDK's editor truncates at 16,000 characters, which is
still a third of the usable context for one tool result, and the schema's
``view_range`` stays unused because nothing tells a small model when to reach
for it.

This subclass of the SDK's ``FileEditorTool`` keeps the registered name
``file_editor`` (config files keep resolving) and changes two things:

* **prompt guidance** — the SDK appends each loaded tool's ``prompt_guidance``
  to the system prompt (issue #943), so the model is told to orient with a
  small ``view_range`` first and read only the ranges it needs;
* **a server-side cap** — a ``view`` issued *without* a ``view_range`` on a
  long file is capped at :data:`MAX_VIEW_LINES` lines, with a notice naming
  the file's real length and the ``view_range`` escape hatch. A bounded view
  is never touched, so a model that already reads ranges pays nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, Self

from openhands.sdk import get_logger
from openhands.sdk.llm import TextContent
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.file_editor.definition import FileEditorAction

logger = get_logger(__name__)

#: Lines an unbounded ``view`` may return before it is cut. Generous enough
#: that ordinary source files stay whole, tight enough that a 1,300-line test
#: module costs one bounded read instead of 16K characters.
MAX_VIEW_LINES = 400

_TRUNCATION_NOTICE = (
    "[View truncated at {shown} of {total} lines. Pass `view_range` as "
    "[start_line, end_line] to read the rest — e.g. `view_range: [{next_start}, "
    "{next_end}]` for the next chunk.]"
)

_FILE_EDITOR_GUIDANCE = """### file_editor usage
- Do not `view` a long file whole. Orient with `view_range: [1, 60]` first (or
  check its length with `wc -l` in the terminal), then `view` only the ranges
  you need.
- Every parameter is required: always send `command` AND `path` in the same
  call.
- Edit with `str_replace` using the smallest unique `old_str` (a few lines of
  context), not whole functions. Use `insert` to append, `create` only for new
  files."""


class BoundedFileEditorTool(FileEditorTool):
    """``FileEditorTool`` with view discipline for small models."""

    # Keep the config-facing name: resolve_toolset emits Tool(name="file_editor")
    # and the registry must keep resolving it to this class.
    name: ClassVar[str] = "file_editor"

    @classmethod
    def create(
        cls,
        conv_state: Any,
        **params: Any,
    ) -> Sequence[Self]:
        tools = super().create(conv_state, **params)
        # The SDK appends prompt_guidance to the inline system prompt (issue
        # #943), deduplicated by text — set on the built instance because the
        # base create() builds the whole description itself.
        return [
            tool.model_copy(update={"prompt_guidance": _FILE_EDITOR_GUIDANCE})
            for tool in tools
        ]

    def __call__(self, action, conversation=None):
        observation = super().__call__(action, conversation)
        if (
            isinstance(action, FileEditorAction)
            and action.command == "view"
            and not action.view_range
        ):
            return _cap_view(observation, MAX_VIEW_LINES)
        return observation


def _cap_view(observation, max_lines: int):
    """Cut an unbounded view observation to ``max_lines`` with a notice."""
    texts = [
        content.text
        for content in observation.content
        if isinstance(content, TextContent)
    ]
    full_text = "\n".join(texts)
    lines = full_text.splitlines()
    if len(lines) <= max_lines:
        return observation
    notice = _TRUNCATION_NOTICE.format(
        shown=max_lines,
        total=len(lines),
        next_start=max_lines + 1,
        next_end=min(2 * max_lines, len(lines)),
    )
    capped = "\n".join(lines[:max_lines]) + "\n" + notice
    logger.info(
        f"file_editor: capped unbounded view of {observation.path} "
        f"({len(lines)} -> {max_lines} lines)"
    )
    return observation.model_copy(
        update={"content": [TextContent(text=capped)]}
    )
