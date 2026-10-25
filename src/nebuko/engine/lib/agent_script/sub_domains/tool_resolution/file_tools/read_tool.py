"""``read`` tool — bounded, offset-addressable file reads (issue #1118).

The SDK ``file_editor view`` defaults to the whole file and only char-truncates
(head+tail split), which is both costly and disorienting — there is no clean
"continue from offset" affordance. This tool caps output server-side (2,000
lines / 50 KB, whichever hits first) and returns ``cat -n``-style numbered
lines, so the model reads a large file in bounded slices and continues with
``offset`` until complete.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from openhands.sdk.llm import TextContent
from openhands.sdk.tool.tool import (
    Action,
    DeclaredResources,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from pydantic import Field
from rich.text import Text

if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

from pathlib import Path

#: Hard caps on one read: whichever limit is hit first ends the slice.
MAX_READ_LINES = 2000
MAX_READ_BYTES = 50_000

_TRUNCATION_NOTICE = (
    "\n[Showing lines {start}-{end} of {total} — output capped at "
    "{cap}. Call read again with offset={next_offset} to continue.]"
)


class ReadAction(Action):
    """Schema for reading a slice of one file."""

    path: str = Field(description="Absolute path to the file to read.")
    offset: int | None = Field(
        default=None,
        ge=1,
        description=(
            "1-indexed line number to start reading from. Omit to read from "
            "the top; use it to continue a read that was capped."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Maximum number of lines to return. Omit to read up to the "
            "server-side cap."
        ),
    )


class ReadObservation(Observation):
    """The numbered file slice, plus the range actually returned."""

    path: str | None = Field(
        default=None, description="The file path that was read."
    )
    start_line: int = Field(default=1, description="First line number returned.")
    end_line: int = Field(default=0, description="Last line number returned.")
    total_lines: int = Field(default=0, description="Total lines in the file.")

    @property
    def visualize(self) -> Text:
        return Text(
            self.text or "[no output]",
            style="bold red" if self.is_error else "bold green",
        )


class ReadExecutor(ToolExecutor):
    """Reads the file, slices it, caps it, and numbers the lines."""

    def __call__(
        self,
        action: ReadAction,
        conversation: Any | None = None,
    ) -> ReadObservation:
        path = Path(action.path)
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ReadObservation.from_text(
                text=f"read failed: file not found: {path}", is_error=True, path=str(path)
            )
        except IsADirectoryError:
            return ReadObservation.from_text(
                text=f"read failed: path is a directory, not a file: {path}",
                is_error=True,
                path=str(path),
            )
        except OSError as exc:
            return ReadObservation.from_text(
                text=f"read failed: could not read {path}: {exc}",
                is_error=True,
                path=str(path),
            )
        except UnicodeDecodeError:
            return ReadObservation.from_text(
                text=f"read failed: {path} is not a UTF-8 text file",
                is_error=True,
                path=str(path),
            )

        lines = content.splitlines()
        total = len(lines)

        start = (action.offset or 1) - 1  # 0-indexed
        start = max(0, min(start, total))
        end = min(start + (action.limit or MAX_READ_LINES), start + MAX_READ_LINES, total)

        selected = lines[start:end]

        # Byte cap: drop whole trailing lines until the slice fits. The
        # truncation notice is appended after the loop, so reserve its size.
        notice_budget = len(
            _TRUNCATION_NOTICE.format(
                start=1, end=1, total=1, cap="2000 lines", next_offset=1
            ).encode("utf-8")
        )
        numbered_parts = [
            f"{number:>6}\t{line}" for number, line in enumerate(selected, start + 1)
        ]

        # Byte cap: drop whole trailing lines until the slice fits. The
        # truncation notice is appended after the loop, so reserve its size.
        notice_budget = len(
            _TRUNCATION_NOTICE.format(
                start=1, end=1, total=1, cap="2000 lines", next_offset=1
            ).encode("utf-8")
        )
        # Running byte count: dropping a line costs its length plus the
        # joining newline, so no re-encode of the whole slice per drop.
        total_bytes = sum(len(part.encode("utf-8")) for part in numbered_parts) + max(
            0, len(numbered_parts) - 1
        )
        while numbered_parts and total_bytes + notice_budget > MAX_READ_BYTES:
            dropped = numbered_parts.pop()
            total_bytes -= len(dropped.encode("utf-8")) + 1
        numbered = "\n".join(numbered_parts)
        end = start + len(numbered_parts)

        if not selected and not numbered:
            numbered = "[empty file]" if total == 0 else "[no lines in range]"

        if end < total:
            numbered += _TRUNCATION_NOTICE.format(
                start=start + 1,
                end=end,
                total=total,
                cap=f"{MAX_READ_LINES} lines" if end - start >= MAX_READ_LINES else f"{MAX_READ_BYTES} bytes",
                next_offset=end + 1,
            )

        return ReadObservation.from_text(
            text=numbered,
            path=str(path),
            start_line=start + 1,
            end_line=end,
            total_lines=total,
        )


READ_TOOL_DESCRIPTION = """Read a file as numbered lines, bounded server-side.

Parameters:
- path: absolute path to the file to read.
- offset: optional 1-indexed line to start from (default: top of file).
- limit: optional maximum number of lines (default: up to the cap).

Output is `cat -n`-style: line number, tab, line content. A read is capped at
2,000 lines / 50 KB, whichever hits first; when the output is capped a notice
at the end states the range shown and the exact `offset` to continue from.
For large files, read in slices and continue with `offset` until complete
rather than requesting the whole file at once.
"""


class ReadTool(ToolDefinition[ReadAction, ReadObservation]):
    """Bounded, offset-addressable file read (issue #1118)."""

    catalog_description: ClassVar[str] = "Read a file as numbered lines, bounded server-side."

    def declared_resources(self, action: Action) -> DeclaredResources:
        """Lock on the target file, mirroring the SDK's file_editor."""
        assert isinstance(action, ReadAction)
        normalized_path = Path(action.path).resolve()
        return DeclaredResources(keys=(f"file:{normalized_path}",), declared=True)

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params: Any,
    ) -> Sequence[Self]:
        """Create a ReadTool with a stateless executor."""
        if params:
            raise ValueError("ReadTool doesn't accept parameters")
        return [
            cls(
                description=READ_TOOL_DESCRIPTION,
                action_type=ReadAction,
                observation_type=ReadObservation,
                executor=ReadExecutor(),
                annotations=ToolAnnotations(
                    title="read",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
