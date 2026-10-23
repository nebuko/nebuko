"""``edit`` tool — several string replacements in one call (issue #1118).

Replaces the SDK ``file_editor``'s one-replacement-per-call ``str_replace`` in
the default tool lists. The flat ``command`` schema was the root cause of the
malformed calls in run 40015215444 (``str_replace`` without ``path``); here the
schema is one shape — ``{path, edits: [{old_str, new_str}, ...]}`` — and one
call carries every replacement for a file, which is also cheaper in turns.

The executor accepts the model quirks observed with smaller models (a
JSON-encoded ``edits`` string, a single edit object instead of a list, the
legacy top-level ``old_str``/``new_str`` pair) through
:func:`normalize_edits`, so a schema slip costs a retry, not the turn.
"""

from __future__ import annotations

import json
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
from openhands.tools.file_editor.exceptions import ToolError
from pydantic import Field, field_validator, model_validator
from rich.text import Text

from .apply_edits import ReplaceEdit, apply_edits

if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class EditAction(Action):
    """Schema for applying several replacements to one file."""

    path: str = Field(description="Absolute path to the file to edit.")
    edits: list[ReplaceEdit] = Field(
        min_length=1,
        description=(
            "Replacements to apply, in order. Every `old_str` is matched "
            "against the file as it is before this call, so the edits must "
            "not overlap each other; all are applied or none are."
        ),
    )

    @field_validator("edits", mode="before")
    @classmethod
    def _normalize_edits(cls, value: Any) -> Any:
        return normalize_edits({"edits": value})["edits"]

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_flat_edit(cls, value: Any) -> Any:
        """Accept the legacy single-edit shape ``{path, old_str, new_str}``.

        Models migrating from ``file_editor``'s ``str_replace`` still send it;
        converting here costs one retry instead of a schema error.
        """
        if isinstance(value, dict) and "edits" not in value and "old_str" in value:
            return normalize_edits(value)
        return value


class EditObservation(Observation):
    """Per-edit results, in the order the edits were given."""

    path: str | None = Field(
        default=None, description="The file path that was edited."
    )
    applied_count: int = Field(
        default=0, description="How many edits were applied."
    )

    @property
    def visualize(self) -> Text:
        return Text(
            self.text or "[no output]",
            style="bold red" if self.is_error else "bold green",
        )


def normalize_edits(arguments: dict[str, Any]) -> dict[str, Any]:
    """Coerce the edit shapes smaller models actually send into ``edits``.

    Handles, in order: a JSON-encoded ``edits`` string, a single edit object
    instead of a list, and the legacy flat ``{old_str, new_str}`` call. The
    input dict is not mutated; anything already a valid list passes through.
    """
    normalized = dict(arguments)

    edits = normalized.get("edits")
    if isinstance(edits, str):
        try:
            edits = json.loads(edits)
        except json.JSONDecodeError:
            raise ToolError(
                "edits is a string but not valid JSON — send it as a list of "
                "{old_str, new_str} objects, or as valid JSON."
            ) from None
        normalized["edits"] = edits

    if isinstance(edits, dict):
        normalized["edits"] = [edits]

    if edits is None and "old_str" in normalized:
        # Legacy single-edit shape from the file_editor str_replace command.
        normalized["edits"] = [
            {
                "old_str": normalized.pop("old_str"),
                "new_str": normalized.pop("new_str", ""),
            }
        ]
    return normalized


class EditExecutor(ToolExecutor):
    """Validates and applies every edit, then writes the file once."""

    def __call__(
        self,
        action: EditAction,
        conversation: Any | None = None,
    ) -> EditObservation:
        path = Path(action.path)
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return EditObservation.from_text(
                text=f"edit failed: file not found: {path}", is_error=True, path=str(path)
            )
        except IsADirectoryError:
            return EditObservation.from_text(
                text=f"edit failed: path is a directory, not a file: {path}",
                is_error=True,
                path=str(path),
            )
        except OSError as exc:
            return EditObservation.from_text(
                text=f"edit failed: could not read {path}: {exc}",
                is_error=True,
                path=str(path),
            )

        try:
            updated = apply_edits(content, action.edits)
        except ToolError as exc:
            return EditObservation.from_text(
                text=f"edit failed: {exc}", is_error=True, path=str(path)
            )

        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return EditObservation.from_text(
                text=f"edit failed: could not write {path}: {exc}",
                is_error=True,
                path=str(path),
            )

        return EditObservation.from_text(
            text=(
                f"Applied {len(action.edits)} edit(s) to {path} "
                f"({len(content)} → {len(updated)} chars)."
            ),
            path=str(path),
            applied_count=len(action.edits),
        )


EDIT_TOOL_DESCRIPTION = """Edit one file by applying several exact-string replacements in ONE call.

Parameters:
- path: absolute path to the file to edit.
- edits: list of {old_str, new_str} replacements, in order.

Rules:
* EXACT MATCHING: each `old_str` must match the file content exactly,
  including all whitespace and indentation.
* UNIQUENESS: each `old_str` must match exactly one location — include
  sufficient context before and after the change point (3-5 lines
  recommended). The call fails and nothing is written if any `old_str` is
  not found, matches several locations, or overlaps another edit.
* ALL-OR-NOTHING: every `old_str` is matched against the file as it is
  before this call, so the edits must not overlap; either all are applied
  or none are.
* Batch every replacement for a file into one call instead of one call per
  replacement — one call costs one model turn.
* Read the file with the `read` tool first to see its exact content.
"""


class EditTool(ToolDefinition[EditAction, EditObservation]):
    """Multi-edit string replacement on one file (issue #1118)."""

    catalog_description: ClassVar[str] = "Apply several string replacements to a file in one call."

    def declared_resources(self, action: Action) -> DeclaredResources:
        """Lock on the target file, mirroring the SDK's file_editor."""
        assert isinstance(action, EditAction)
        normalized_path = Path(action.path).resolve()
        return DeclaredResources(keys=(f"file:{normalized_path}",), declared=True)

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params: Any,
    ) -> Sequence[Self]:
        """Create an EditTool with a stateless executor."""
        if params:
            raise ValueError("EditTool doesn't accept parameters")
        return [
            cls(
                description=EDIT_TOOL_DESCRIPTION,
                action_type=EditAction,
                observation_type=EditObservation,
                executor=EditExecutor(),
                annotations=ToolAnnotations(
                    title="edit",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]
