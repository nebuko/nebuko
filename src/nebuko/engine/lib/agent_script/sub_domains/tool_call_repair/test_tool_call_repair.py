"""Tests for the tool-call argument repair hooks.

Every malformed shape pinned here is taken from the GLM-5.3-flash console log
of the issue #1114 context run — 18 of ~130 model actions died on these before
the repair existed, each costing a full LLM round-trip.
"""

import json

import pytest

from nebuko.engine.lib.agent_script.sub_domains.tool_call_repair.tool_call_repair import (
    install,
    parse_tool_call_arguments,
)
from openhands.sdk.agent import agent as sdk_agent
from openhands.sdk.tool.tool import Action
from openhands.tools.file_editor.definition import FileEditorAction


class TestParseRepair:
    def test_valid_arguments_pass_through(self):
        parsed = parse_tool_call_arguments('{"commands": ["ls", "pwd"]}')
        assert parsed == {"commands": ["ls", "pwd"]}

    def test_glue_key_with_newline_is_dropped(self):
        # "defaults\nsummary" — the model welded its narration into the JSON.
        raw = json.dumps({"commands": ["ls"], "defaults\nsummary": "Find hooks"})
        parsed = parse_tool_call_arguments(raw)
        assert parsed == {"commands": ["ls"]}

    def test_trailing_garbage_after_closed_object_is_cut(self):
        # The worst observed shape: the model re-opened the JSON object after
        # closing it. The closed prefix is recovered; the glue tail is cut.
        raw = '{"commands": [], "summary": "noop"}\\nsummary\': \'noop\'}'
        parsed = parse_tool_call_arguments(raw)
        assert parsed["commands"] == []
        # `summary` is a clean key the SDK pops itself (_extract_summary).
        assert parsed["summary"] == "noop"

    def test_unterminated_string_value_is_closed(self):
        # The `think` failure: 'Unterminated string starting at line 1 column 13'
        parsed = parse_tool_call_arguments('{"thought": "maybe the status -z')
        assert parsed == {"thought": "maybe the status -z"}

    def test_truncated_list_element_is_cut_back(self):
        parsed = parse_tool_call_arguments('{"commands": ["ls", "pwd')
        assert parsed == {"commands": ["ls"]}

    def test_truncated_key_gets_an_empty_value(self):
        parsed = parse_tool_call_arguments('{"commands": ["ls"], "defaul')
        # The completed keys survive; a clean unknown key like `defaul` is the
        # action-schema layer's problem (dropped there), not the parser's.
        assert parsed["commands"] == ["ls"]
        assert parsed["defaul"] == ""

    def test_unrepairable_json_still_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_tool_call_arguments("{ definitely not json ]")

    def test_non_dict_json_returns_empty_dict(self):
        # SDK contract: a parseable non-dict is {} — not an exception.
        assert parse_tool_call_arguments('["ls"]') == {}


class TestActionFromArgumentsRepair:
    @pytest.fixture(autouse=True)
    def _installed(self):
        # The unknown-key repair hooks the ToolDefinition base method; it is
        # the engine's install() (run_agent) that activates it.
        install()

    def _tool(self, action_type):
        from nebuko.engine.lib.agent_script.features.bounded_file_editor.bounded_file_editor import (
            BoundedFileEditorTool,
        )

        return BoundedFileEditorTool(action_type=action_type, description="dummy")

    def test_unknown_clean_key_is_dropped(self):
        tool = self._tool(FileEditorAction)
        action = tool.action_from_arguments(
            {"command": "view", "path": "/tmp/f", "defaults": "stray narration"}
        )
        assert action.command == "view"
        assert action.path == "/tmp/f"

    def test_prefix_misspelling_is_renamed(self):
        # GLM wrote `new` where the schema declares `new_str`.
        tool = self._tool(FileEditorAction)
        action = tool.action_from_arguments(
            {"command": "str_replace", "path": "/tmp/f", "old_str": "y", "new": "x"}
        )
        assert action.new_str == "x"

    def test_ambiguous_prefix_is_dropped_not_guessed(self):
        from pydantic import BaseModel

        class _Ambiguous(Action):
            command: str
            commands: list[str] = []

        tool = self._tool(_Ambiguous)
        action = tool.action_from_arguments({"command": "x", "comma": "y"})
        assert action.command == "x"
        assert action.commands == []


class TestInstall:
    def test_install_patches_the_sdk_agent_seams(self):
        install()
        assert sdk_agent.parse_tool_call_arguments is parse_tool_call_arguments
        from openhands.sdk.agent import utils as sdk_utils

        assert sdk_utils.parse_tool_call_arguments is parse_tool_call_arguments

    def test_install_is_idempotent(self):
        install()
        first = sdk_agent.parse_tool_call_arguments
        install()
        assert sdk_agent.parse_tool_call_arguments is first
