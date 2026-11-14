"""Pure multi-edit matcher/applier for the ``edit`` tool (issue #1118).

Every ``old_str`` is matched against the *original* content — not the result of
the previous edit — so the model can express edits in any order without one
edit invalidating another's anchor. Matches are then applied in reverse order,
which keeps earlier replacements from shifting later offsets. The function is
pure (string in, string out) and raises :class:`ToolError` on any invalid edit,
so a failed batch never writes a partial result.
"""

from openhands.tools.file_editor.exceptions import ToolError
from pydantic import BaseModel, Field, model_validator


class ReplaceEdit(BaseModel):
    """One exact-string replacement within the target file."""

    old_str: str = Field(
        description=(
            "The exact string to replace — must match the file content "
            "exactly, including whitespace and indentation, and must be "
            "unique in the file (include 3-5 surrounding lines if needed)."
        )
    )
    new_str: str = Field(description="The string that replaces `old_str`.")

    @model_validator(mode="after")
    def _strings_must_differ(self) -> "ReplaceEdit":
        if self.old_str == self.new_str:
            raise ValueError("old_str and new_str must be different")
        return self


def _match_spans(content: str, old_str: str) -> list[tuple[int, int]]:
    """All ``(start, end)`` spans where ``old_str`` occurs in ``content``."""
    spans: list[tuple[int, int]] = []
    start = content.find(old_str)
    while start != -1:
        spans.append((start, start + len(old_str)))
        start = content.find(old_str, start + 1)
    return spans


def apply_edits(content: str, edits: list[ReplaceEdit]) -> str:
    """Apply every edit against ``content``; return the new content.

    Raises:
        ToolError: when an ``old_str`` is not found, matches more than once,
            or two edits overlap. Nothing is written on error — the caller
            only persists the returned string.
    """
    all_spans: list[tuple[int, int, int]] = []  # (start, end, edit_index)
    for index, edit in enumerate(edits):
        spans = _match_spans(content, edit.old_str)
        if not spans:
            raise ToolError(
                f"Could not find edits[{index}] old_str in the file: "
                f"{edit.old_str!r}. The old_str must match the file content "
                "exactly, including whitespace and indentation."
            )
        if len(spans) > 1:
            raise ToolError(
                f"Found {len(spans)} occurrences of edits[{index}] old_str "
                f"{edit.old_str!r}. It must match exactly one location — "
                "include more surrounding context to make it unique."
            )
        all_spans.append((spans[0][0], spans[0][1], index))

    ordered = sorted(all_spans)
    for (start_a, end_a, index_a), (start_b, _, index_b) in zip(
        ordered, ordered[1:]
    ):
        if start_b < end_a:
            raise ToolError(
                f"edits[{index_a}] and edits[{index_b}] overlap in the file. "
                "Split them into non-overlapping edits or merge them into one."
            )

    # Reverse order: replacing from the end keeps every earlier span valid.
    result = content
    for start, end, index in reversed(ordered):
        result = result[:start] + edits[index].new_str + result[end:]
    return result
