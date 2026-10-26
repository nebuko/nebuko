"""``write`` tool — create or fully overwrite one file (issue #1118).

Keeps file creation first-class once ``file_editor`` leaves the default tool
lists (its ``create`` command went with it). Overwrites are allowed but refuse
to clobber changed content silently: an existing file must either be identical
or the caller must have read it first — the error says which.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
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

from .read_tool import ReadAction

if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class WriteAction(Action):
    """Schema for creating or overwriting one file."""

    path: str = Field(description="Absolute path to the file to write.")
    file_text: str = Field(description="The full content to write to the file.")


class WriteObservation(Observation):
    """The written path and whether the file was created or overwritten."""

    path: str | None = Field(
        default=None, description="The file path that was written."
    )
    created: bool = Field(
        default=False,
        description="True when the file did not exist before this call.",
    )

    @property
    def visualize(self) -> Text:
        return Text(
            self.text or "[no output]",
            style="bold red" if self.is_error else "bold green",
        )


def _was_read(path: Path, conversation: Any | None) -> bool:
    """Whether this conversation already read ``path`` with the read tool.

    Scans the conversation's action events for a successful ``read`` on the
    same resolved path. Without a conversation (direct/standalone calls) there
    is no history to consult, so the guard cannot apply and the write is
    allowed — the SDK always passes a conversation in real agent runs.
    """
    if conversation is None:
        return False
    try:
        events = conversation.state.events
    except AttributeError:
        return False
    resolved = str(path.resolve())
    for event in events:
        tool_name = getattr(event, "tool_name", None)
        action = getattr(event, "action", None)
        if tool_name != "read" or not isinstance(action, ReadAction):
            continue
        if str(Path(action.path).resolve()) == resolved:
            return True
    return False


class WriteExecutor(ToolExecutor):
    """Creates parent directories as needed; guards silent content loss."""

    def __call__(
        self,
        action: WriteAction,
        conversation: Any | None = None,
    ) -> WriteObservation:
        path = Path(action.path)
        existed = path.exists()
        if existed:
            try:
                current = path.read_text(encoding="utf-8")
            except OSError as exc:
                return WriteObservation.from_text(
                    text=f"write failed: could not read existing {path}: {exc}",
                    is_error=True,
                    path=str(path),
                )
            except UnicodeDecodeError:
                current = None  # binary or non-UTF-8: treat as different content
            if current is not None and current == action.file_text:
                return WriteObservation.from_text(
                    text=f"write: {path} already has this exact content "
                    f"({len(action.file_text)} chars); nothing written.",
                    path=str(path),
                    created=False,
                )
            if (
                current is not None
                and current != action.file_text
                and conversation is not None
                and not _was_read(path, conversation)
            ):
                return WriteObservation.from_text(
                    text=(
                        f"write refused: {path} exists with different content "
                        "and has not been read in this conversation. Read the "
                        "file with the `read` tool first, then write again — "
                        "or confirm the full replacement content."
                    ),
                    is_error=True,
                    path=str(path),
                )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(action.file_text, encoding="utf-8")
        except OSError as exc:
            return WriteObservation.from_text(
                text=f"write failed: could not write {path}: {exc}",
                is_error=True,
                path=str(path),
            )

        verb = "Created" if not existed else "Overwrote"
        return WriteObservation.from_text(
            text=f"{verb} {path} ({len(action.file_text)} chars).",
            path=str(path),
            created=not existed,
        )


WRITE_TOOL_DESCRIPTION = """Create or fully overwrite one file.

Parameters:
- path: absolute path to the file to write.
- file_text: the FULL content to write — this replaces the whole file, so
  include every line you want to keep.

Rules:
* Creating a new file: parent directories are created as needed.
* Overwriting an existing file with different content requires reading it
  with the `read` tool first in this conversation — the call is refused
  otherwise, so changed content is never clobbered silently.
* Writing identical content is a no-op that reports "nothing written".
* For partial changes prefer the `edit` tool; use `write` for new files or
  full rewrites.
"""


class WriteTool(ToolDefinition[WriteAction, WriteObservation]):
    """Create or overwrite one file (issue #1118)."""

    catalog_description: ClassVar[str] = "Create or fully overwrite one file."

    def declared_resources(self, action: Action) -> DeclaredResources:
        """Lock on the target file, mirroring the SDK's file_editor."""
        assert isinstance(action, WriteAction)
        normalized_path = Path(action.path).resolve()
        return DeclaredResources(keys=(f"file:{normalized_path}",), declared=True)

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params: Any,
    ) -> Sequence[Self]:
        """Create a WriteTool with a stateless executor."""
        if params:
            raise ValueError("WriteTool doesn't accept parameters")
        return [
            cls(
                description=WRITE_TOOL_DESCRIPTION,
                action_type=WriteAction,
                observation_type=WriteObservation,
                executor=WriteExecutor(),
                annotations=ToolAnnotations(
                    title="write",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
