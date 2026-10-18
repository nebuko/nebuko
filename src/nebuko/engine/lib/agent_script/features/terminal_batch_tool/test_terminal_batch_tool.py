"""Tests for the ``terminal_batch`` tool (issue #982).

Two things are pinned here beyond the happy path:

* the **description** — the SDK hardcodes ``UNIX_TOOL_DESCRIPTION``
  ("One command at a time") in ``TerminalTool.create``, and that text is the
  counter-incentive the tool exists to remove, so a silent fallback to it must
  fail loudly;
* the **schema** — the SDK builds the JSON schema the model sees from
  ``TerminalBatchAction``, so ``commands`` has to stay a list of strings and a
  bare string must still be accepted as one entry.

The behavioural tests drive a real ``TerminalExecutor`` (subprocess backend, so
no tmux server is required) and assert the property the whole change rests on:
three commands cost one action, and ``cd`` carries across entries.
"""

import tempfile
from pathlib import Path

import pytest
from openhands.tools.terminal import TerminalExecutor

from agent_script.features.terminal_batch_tool.terminal_batch_tool import (
    TERMINAL_BATCH_DESCRIPTION,
    TerminalBatchAction,
    TerminalBatchExecutor,
    TerminalBatchObservation,
    TerminalBatchTool,
)
from agent_script.sub_domains.tool_resolution.tool_resolution import (
    resolve_toolset,
)


@pytest.fixture
def bound_tool(tmp_path: Path) -> TerminalBatchTool:
    """A TerminalBatchTool bound to a real single-session executor."""
    tool = TerminalBatchTool.create()[0]
    tool.executor._executor = TerminalExecutor(
        working_dir=str(tmp_path), terminal_type="subprocess"
    )
    return tool


class TestDescription:
    def test_description_overrides_the_sdk_one(self):
        assert "terminal_batch" not in TERMINAL_BATCH_DESCRIPTION
        assert "Run several shell commands in ONE call" in TERMINAL_BATCH_DESCRIPTION
        # The SDK clause that says the opposite must be gone...
        assert "One command at a time" not in TERMINAL_BATCH_DESCRIPTION
        # ...but the guidance it carries (soft timeout, reset) must survive.
        assert "Soft timeout" in TERMINAL_BATCH_DESCRIPTION
        assert "Terminal Reset" in TERMINAL_BATCH_DESCRIPTION

    def test_create_uses_our_description_not_the_sdk_default(self):
        tool = TerminalBatchTool.create()[0]
        assert tool.description == TERMINAL_BATCH_DESCRIPTION

    def test_name_derives_from_the_class(self):
        assert TerminalBatchTool.name == "terminal_batch"

    def test_create_rejects_parameters(self):
        with pytest.raises(ValueError):
            TerminalBatchTool.create(working_dir="/tmp")


class TestActionSchema:
    def test_commands_is_a_list_of_strings(self):
        props = TerminalBatchAction.model_json_schema()["properties"]
        assert props["commands"]["type"] == "array"
        assert props["commands"]["items"] == {"type": "string"}

    def test_single_string_is_coerced_to_one_entry(self):
        assert TerminalBatchAction(commands="ls -la").commands == ["ls -la"]

    def test_stop_on_failure_defaults_off(self):
        action = TerminalBatchAction(commands=["ls"])
        assert action.stop_on_failure is False
        assert action.timeout is None


class TestExecutor:
    def test_three_commands_return_three_results_and_keep_cwd(self, bound_tool, tmp_path):
        action = TerminalBatchAction(
            commands=["pwd", "mkdir -p sub", "cd sub"], timeout=20
        )
        observation = bound_tool.executor(action)

        assert isinstance(observation, TerminalBatchObservation)
        assert len(observation.results) == 3
        assert observation.exit_codes == [0, 0, 0]
        assert observation.failed_count == 0
        assert observation.is_error is False
        assert "[1/3] $ pwd" in observation.results[0]
        assert str(tmp_path) in observation.results[0]
        # Shell state carried into the next action, exactly like `terminal`.
        assert observation.working_dir == str(tmp_path / "sub")

        follow_up = bound_tool.executor(TerminalBatchAction(commands=["pwd"], timeout=20))
        assert str(tmp_path / "sub") in follow_up.results[0]

    def test_failure_does_not_abort_the_batch_by_default(self, bound_tool):
        observation = bound_tool.executor(
            TerminalBatchAction(commands=["false", "echo after"], timeout=20)
        )
        assert observation.exit_codes == [1, 0]
        assert observation.failed_count == 1
        assert observation.is_error is True
        assert "after" in observation.results[1]
        assert "1/2 command(s) failed" in observation.text

    def test_stop_on_failure_skips_the_rest(self, bound_tool):
        observation = bound_tool.executor(
            TerminalBatchAction(
                commands=["false", "echo never"], stop_on_failure=True, timeout=20
            )
        )
        assert observation.exit_codes == [1, None]
        assert "skipped" in observation.results[1]
        assert observation.failed_count == 1

    def test_newline_inside_an_entry_is_rejected_not_executed(self, bound_tool, tmp_path):
        observation = bound_tool.executor(
            TerminalBatchAction(commands=["pwd\nmkdir sub"], timeout=20)
        )
        assert observation.exit_codes == [None]
        assert "rejected" in observation.results[0]
        assert not (tmp_path / "sub").exists()

    def test_observation_carries_the_working_directory(self, bound_tool, tmp_path):
        observation = bound_tool.executor(
            TerminalBatchAction(commands=["pwd"], timeout=20)
        )
        rendered = "".join(c.text for c in observation.to_llm_content)
        assert f"[Current working directory: {tmp_path}]" in rendered

    def test_unbound_executor_reports_an_error(self):
        observation = TerminalBatchExecutor()(TerminalBatchAction(commands=["ls"]))
        assert observation.is_error is True
        assert "could not start a shell session" in observation.text


class TestResolution:
    def test_terminal_batch_resolves_through_tool_resolution(self):
        resolved = resolve_toolset(["terminal", "terminal_batch"], "context")
        assert [t.name for t in resolved.sdk_tools] == [
            "terminal",
            "TerminalBatchTool",
        ]
        assert resolved.has("TerminalBatchTool")
        assert resolved.dispatch_tool_enabled is False

    def test_registered_under_its_own_name(self):
        from openhands.sdk.tool.registry import list_registered_tools

        resolve_toolset(["terminal_batch"], "context")
        assert "terminal_batch" in list_registered_tools()
