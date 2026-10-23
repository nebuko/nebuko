"""Tests for the nebuko read/edit/write file tools (issue #1118)."""

import json
import tempfile
from pathlib import Path

import pytest
from openhands.tools.file_editor.exceptions import ToolError

from agent_script.sub_domains.tool_resolution.file_tools import (
    MAX_READ_BYTES,
    MAX_READ_LINES,
    EditAction,
    EditTool,
    ReadAction,
    ReadTool,
    ReplaceEdit,
    WriteAction,
    WriteTool,
    apply_edits,
    normalize_edits,
)


@pytest.fixture
def tmp_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    return path


def _read(path: Path, **kwargs):
    return ReadTool.create()[0](ReadAction(path=str(path), **kwargs))


def _edit(path: Path, edits, **kwargs):
    return EditTool.create()[0](EditAction(path=str(path), edits=edits, **kwargs))


def _write(path: Path, file_text: str):
    return WriteTool.create()[0](WriteAction(path=str(path), file_text=file_text))


class TestReadTool:
    def test_reads_numbered_lines(self, tmp_file: Path):
        observation = _read(tmp_file)
        assert not observation.is_error
        assert "     1\talpha" in observation.text
        assert "     4\tdelta" in observation.text
        assert (observation.start_line, observation.end_line) == (1, 4)
        assert observation.total_lines == 4

    def test_offset_is_one_indexed(self, tmp_file: Path):
        observation = _read(tmp_file, offset=2)
        assert observation.text.startswith("     2\tbeta")
        assert observation.start_line == 2

    def test_limit_bounds_the_slice(self, tmp_file: Path):
        observation = _read(tmp_file, offset=2, limit=2)
        assert "     2\tbeta" in observation.text
        assert "     3\tgamma" in observation.text
        assert "alpha" not in observation.text
        assert "delta" not in observation.text
        assert observation.end_line == 3

    def test_line_cap_adds_continue_notice(self, tmp_path: Path):
        path = tmp_path / "big.txt"
        path.write_text(
            "\n".join(f"line {i}" for i in range(1, MAX_READ_LINES + 501)),
            encoding="utf-8",
        )
        observation = _read(path)
        assert observation.end_line == MAX_READ_LINES
        assert f"offset={MAX_READ_LINES + 1}" in observation.text
        assert "2000 lines" in observation.text

    def test_byte_cap_drops_trailing_lines(self, tmp_path: Path):
        path = tmp_path / "wide.txt"
        line = "x" * 200
        path.write_text("\n".join([line] * 500), encoding="utf-8")
        observation = _read(path)
        assert len(observation.text.encode("utf-8")) <= MAX_READ_BYTES
        assert observation.end_line < 500
        assert "bytes" in observation.text

    def test_offset_past_end_returns_empty_range(self, tmp_file: Path):
        observation = _read(tmp_file, offset=100)
        assert not observation.is_error
        assert observation.end_line == observation.total_lines

    def test_missing_file_is_an_error_observation(self, tmp_path: Path):
        observation = _read(tmp_path / "nope.txt")
        assert observation.is_error
        assert "file not found" in observation.text

    def test_directory_is_an_error_observation(self, tmp_path: Path):
        observation = _read(tmp_path)
        assert observation.is_error
        assert "directory" in observation.text


class TestApplyEdits:
    def test_applies_all_edits_against_original_content(self):
        content = "alpha\nbeta\ngamma\n"
        result = apply_edits(
            content,
            [
                ReplaceEdit(old_str="alpha", new_str="ALPHA"),
                ReplaceEdit(old_str="gamma", new_str="GAMMA"),
            ],
        )
        assert result == "ALPHA\nbeta\nGAMMA\n"

    def test_reverse_order_keeps_earlier_spans_valid(self):
        # The second edit's anchor sits after text the first edit lengthens.
        content = "aa\nbb\n"
        result = apply_edits(
            content,
            [
                ReplaceEdit(old_str="aa", new_str="aa-extended"),
                ReplaceEdit(old_str="bb", new_str="cc"),
            ],
        )
        assert result == "aa-extended\ncc\n"

    def test_not_found_raises_with_index(self):
        with pytest.raises(ToolError, match="Could not find edits\\[1\\]"):
            apply_edits(
                "content",
                [
                    ReplaceEdit(old_str="content", new_str="x"),
                    ReplaceEdit(old_str="missing", new_str="y"),
                ],
            )

    def test_non_unique_raises_with_occurrence_count(self):
        with pytest.raises(ToolError, match="Found 2 occurrences of edits\\[0\\]"):
            apply_edits("dup dup", [ReplaceEdit(old_str="dup", new_str="x")])

    def test_overlap_raises_and_names_both_edits(self):
        with pytest.raises(ToolError, match="edits\\[0\\] and edits\\[1\\] overlap"):
            apply_edits(
                "abcdef",
                [
                    ReplaceEdit(old_str="abcd", new_str="x"),
                    ReplaceEdit(old_str="cdef", new_str="y"),
                ],
            )

    def test_identical_adjacent_anchors_do_not_count_as_overlap(self):
        result = apply_edits(
            "ab",
            [
                ReplaceEdit(old_str="a", new_str="A"),
                ReplaceEdit(old_str="b", new_str="B"),
            ],
        )
        assert result == "AB"


class TestEditTool:
    def test_edits_file_and_reports_count(self, tmp_file: Path):
        observation = _edit(
            tmp_file, [{"old_str": "beta", "new_str": "BETA"}]
        )
        assert not observation.is_error
        assert observation.applied_count == 1
        assert tmp_file.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\ndelta\n"

    def test_multi_edit_in_one_call(self, tmp_file: Path):
        observation = _edit(
            tmp_file,
            [
                {"old_str": "alpha", "new_str": "ALPHA"},
                {"old_str": "delta", "new_str": "DELTA"},
            ],
        )
        assert observation.applied_count == 2
        assert tmp_file.read_text(encoding="utf-8") == "ALPHA\nbeta\ngamma\nDELTA\n"

    def test_atomic_nothing_written_on_failure(self, tmp_file: Path):
        before = tmp_file.read_text(encoding="utf-8")
        observation = _edit(
            tmp_file,
            [
                {"old_str": "alpha", "new_str": "ALPHA"},
                {"old_str": "missing", "new_str": "X"},
            ],
        )
        assert observation.is_error
        assert tmp_file.read_text(encoding="utf-8") == before

    def test_json_string_edits_are_accepted(self, tmp_file: Path):
        edits = json.dumps([{"old_str": "beta", "new_str": "BETA"}])
        observation = _edit(tmp_file, edits)
        assert not observation.is_error
        assert "BETA" in tmp_file.read_text(encoding="utf-8")

    def test_single_edit_object_is_accepted(self, tmp_file: Path):
        observation = _edit(tmp_file, {"old_str": "beta", "new_str": "BETA"})
        assert not observation.is_error
        assert "BETA" in tmp_file.read_text(encoding="utf-8")

    def test_legacy_top_level_old_str_is_accepted(self, tmp_file: Path):
        observation = EditTool.create()[0](
            EditAction.model_validate(
                {"path": str(tmp_file), "old_str": "beta", "new_str": "BETA"}
            )
        )
        assert not observation.is_error
        assert "BETA" in tmp_file.read_text(encoding="utf-8")

    def test_missing_file_is_an_error_observation(self, tmp_path: Path):
        observation = _edit(tmp_path / "nope.txt", [{"old_str": "a", "new_str": "b"}])
        assert observation.is_error
        assert "file not found" in observation.text


class TestNormalizeEdits:
    def test_json_string_is_decoded(self):
        edits = json.dumps([{"old_str": "a", "new_str": "b"}])
        assert normalize_edits({"edits": edits})["edits"] == [
            {"old_str": "a", "new_str": "b"}
        ]

    def test_invalid_json_string_raises_tool_error(self):
        with pytest.raises(ToolError, match="not valid JSON"):
            normalize_edits({"edits": "{not json"})

    def test_single_dict_is_wrapped(self):
        assert normalize_edits({"edits": {"old_str": "a", "new_str": "b"}})["edits"] == [
            {"old_str": "a", "new_str": "b"}
        ]

    def test_legacy_flat_shape_is_converted(self):
        normalized = normalize_edits({"old_str": "a", "new_str": "b"})
        assert normalized["edits"] == [{"old_str": "a", "new_str": "b"}]
        assert "old_str" not in normalized

    def test_valid_list_passes_through(self):
        edits = [{"old_str": "a", "new_str": "b"}]
        assert normalize_edits({"edits": edits})["edits"] is edits


class TestWriteTool:
    def test_creates_new_file(self, tmp_path: Path):
        path = tmp_path / "new" / "file.txt"
        observation = _write(path, "content\n")
        assert not observation.is_error
        assert observation.created is True
        assert path.read_text(encoding="utf-8") == "content\n"

    def test_overwrites_existing_file(self, tmp_file: Path):
        observation = _write(tmp_file, "replaced\n")
        assert not observation.is_error
        assert observation.created is False
        assert tmp_file.read_text(encoding="utf-8") == "replaced\n"

    def test_identical_content_is_a_no_op(self, tmp_file: Path):
        before = tmp_file.stat().st_mtime_ns
        observation = _write(tmp_file, tmp_file.read_text(encoding="utf-8"))
        assert not observation.is_error
        assert "nothing written" in observation.text
        assert tmp_file.stat().st_mtime_ns == before

    def test_directory_creation_is_reported_not_fatal(self, tmp_path: Path):
        path = tmp_path / "a" / "b" / "c.txt"
        observation = _write(path, "x")
        assert not observation.is_error
        assert path.exists()


class TestRegistration:
    def test_tools_resolve_from_the_sdk_registry(self):
        from openhands.sdk.tool.registry import list_registered_tools

        from agent_script.sub_domains.tool_resolution.tool_resolution import (
            ensure_sdk_tools_registered,
        )

        ensure_sdk_tools_registered()
        registered = list_registered_tools()
        for name in ("read", "edit", "write", "file_editor"):
            assert name in registered, name

    def test_resolve_toolset_emits_the_new_specs(self):
        from agent_script.sub_domains.tool_resolution.tool_resolution import (
            resolve_toolset,
        )

        resolved = resolve_toolset(["read", "edit", "write"], "dev")
        assert resolved.has("read")
        assert resolved.has("edit")
        assert resolved.has("write")
        assert not resolved.has("file_editor")

    def test_file_editor_still_resolves_for_opt_in_configs(self):
        from agent_script.sub_domains.tool_resolution.tool_resolution import (
            resolve_toolset,
        )

        resolved = resolve_toolset(["file_editor"], "dev")
        assert resolved.has("file_editor")
