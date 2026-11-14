"""Tests for the bounded file_editor (issue #1114 context economics).

An unbounded whole-file ``view`` of a 1,322-line file cost ~16K context
characters per call in the measured run; the subclass caps those views while
leaving explicit ``view_range`` reads untouched.
"""

from pathlib import Path

import pytest

from nebuko.engine.lib.agent_script.features.bounded_file_editor.bounded_file_editor import (
    MAX_VIEW_LINES,
    BoundedFileEditorTool,
)
from openhands.tools.file_editor.definition import FileEditorAction
from openhands.tools.file_editor.impl import FileEditorExecutor


@pytest.fixture
def long_file(tmp_path: Path) -> Path:
    path = tmp_path / "big.py"
    path.write_text("\n".join(f"line {i}" for i in range(1, 1301)) + "\n")
    return path


@pytest.fixture
def tool(long_file: Path) -> BoundedFileEditorTool:
    executor = FileEditorExecutor(workspace_root=str(long_file.parent))
    return BoundedFileEditorTool(
        action_type=FileEditorAction,
        description="dummy",
        executor=executor,
    )


def _text(observation) -> str:
    return observation.content[0].text


class TestBoundedView:
    def test_unbounded_view_of_a_long_file_is_capped(self, tool, long_file):
        observation = tool(FileEditorAction(command="view", path=str(long_file)))
        text = _text(observation)
        lines = text.splitlines()
        # The cap counts the rendered text (header line + MAX_VIEW_LINES),
        # and the notice names the real length and the escape hatch.
        assert len(lines) == MAX_VIEW_LINES + 1
        # The cap counts the rendered text: the editor's header line plus 399
        # file lines, so content stops before line 400.
        assert "line 399" in text
        assert "line 400" not in text
        assert "View truncated" in lines[-1]
        assert "view_range" in lines[-1]

    def test_explicit_view_range_is_never_touched(self, tool, long_file):
        observation = tool(
            FileEditorAction(
                command="view", path=str(long_file), view_range=[1, 500]
            )
        )
        # 500 > MAX_VIEW_LINES: an explicit range overrides the cap.
        assert len(_text(observation).splitlines()) >= 500

    def test_short_file_view_is_uncapped(self, tool, tmp_path):
        small = tmp_path / "small.py"
        small.write_text("x = 1\n")
        observation = tool(FileEditorAction(command="view", path=str(small)))
        text = _text(observation)
        assert "x = 1" in text
        assert "View truncated" not in text

    def test_edits_are_not_capped(self, tool, long_file):
        observation = tool(
            FileEditorAction(
                command="str_replace",
                path=str(long_file),
                old_str="line 1\n",
                new_str="line one\n",
            )
        )
        assert not observation.is_error
        assert "line one" in long_file.read_text()


class TestPromptProfile:
    """``prompt_profile: lean`` (issue #851 S3) delivered via Tool params."""

    def test_lean_description_is_compact_and_keeps_the_rules(self):
        full = _create_tool("full").description
        lean = _create_tool("lean").description
        assert len(lean) < len(full) / 2
        for rule in ("view", "create", "str_replace", "undo_edit", "absolute"):
            assert rule in lean
        # The working-directory hint the SDK appends must survive.
        assert "working directory" in lean

    def test_lean_keeps_the_prompt_guidance(self):
        tool = _create_tool("lean")
        assert "view_range" in tool.prompt_guidance

    def test_full_is_the_default(self):
        assert "EXACT" in _create_tool("full").description or "exact" in _create_tool(
            "full"
        ).description

    def test_unknown_profile_is_rejected(self):
        with pytest.raises(ValueError, match="prompt_profile"):
            BoundedFileEditorTool.create(_state(), prompt_profile="verbose")


def _state():
    class _Workspace:
        working_dir = "/w"

    class _State:
        workspace = _Workspace()

        class agent:
            class llm:
                @staticmethod
                def vision_is_active():
                    return False

    return _State()


def _create_tool(profile: str) -> BoundedFileEditorTool:
    return BoundedFileEditorTool.create(_state(), prompt_profile=profile)[0]


class TestRegistrationSurface:
    def test_keeps_the_file_editor_name(self):
        assert BoundedFileEditorTool.name == "file_editor"

    def test_create_attaches_prompt_guidance(self):
        class _State:
            class workspace:
                working_dir = "/tmp"

            class agent:
                class llm:
                    @staticmethod
                    def vision_is_active():
                        return False

        tools = BoundedFileEditorTool.create(_State)
        assert len(tools) == 1
        assert "view_range" in tools[0].prompt_guidance
        assert "str_replace" in tools[0].prompt_guidance
