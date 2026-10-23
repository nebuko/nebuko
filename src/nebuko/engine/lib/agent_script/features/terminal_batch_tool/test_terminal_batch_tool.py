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
    build_terminal_batch_description,
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


class TestPromptProfileAndRecon:
    """The ``token_budget`` knobs delivered via the Tool spec's params
    (issue #851 S3): ``prompt_profile="lean"`` trims the description,
    ``require_batched_recon=True`` appends the batching directive."""

    def test_lean_profile_trims_the_description(self):
        lean = build_terminal_batch_description(lean=True)
        assert len(lean) < len(TERMINAL_BATCH_DESCRIPTION) / 2
        # The parameter contract must survive the trim.
        for param in ("commands", "stop_on_failure", "timeout", "summary"):
            assert param in lean
        # The SDK tail the lean profile drops.
        assert "Soft timeout" not in lean

    def test_full_profile_is_the_default_create(self):
        tool = TerminalBatchTool.create()[0]
        assert tool.description == TERMINAL_BATCH_DESCRIPTION
        assert "Reconnaissance" not in tool.prompt_guidance

    def test_lean_and_recon_flow_through_create_params(self):
        tool = TerminalBatchTool.create(
            prompt_profile="lean", require_batched_recon=True
        )[0]
        assert tool.description == build_terminal_batch_description(lean=True)
        assert "Reconnaissance discipline" in tool.prompt_guidance
        assert "4+ commands" in tool.prompt_guidance

    def test_recon_alone_keeps_the_full_description(self):
        tool = TerminalBatchTool.create(require_batched_recon=True)[0]
        assert tool.description == TERMINAL_BATCH_DESCRIPTION
        assert "Reconnaissance discipline" in tool.prompt_guidance

    def test_unknown_profile_is_rejected(self):
        with pytest.raises(ValueError, match="prompt_profile"):
            TerminalBatchTool.create(prompt_profile="verbose")

    def test_unknown_create_params_are_still_rejected(self):
        with pytest.raises(ValueError, match="working_dir"):
            TerminalBatchTool.create(working_dir="/tmp")


class TestActionSchema:
    def test_commands_is_a_list_of_strings(self):
        props = TerminalBatchAction.model_json_schema()["properties"]
        assert props["commands"]["type"] == "array"
        assert props["commands"]["items"] == {"type": "string"}

    def test_single_string_is_coerced_to_one_entry(self):
        assert TerminalBatchAction(commands="ls -la").commands == ["ls -la"]

    def test_summary_is_an_optional_declared_field(self):
        # Declaring `summary` gives the model's narration a legitimate home
        # (issue #1114 run: the model welded its "Summary:" line into the JSON
        # keys instead). The SDK reads a declared summary as the event summary.
        action = TerminalBatchAction(commands=["ls"], summary="list the checkout")
        assert action.summary == "list the checkout"
        assert TerminalBatchAction(commands=["ls"]).summary is None
        props = TerminalBatchAction.model_json_schema()["properties"]
        assert "summary" in props

    def test_description_documents_the_summary_parameter(self):
        assert "summary" in TERMINAL_BATCH_DESCRIPTION

    def test_create_attaches_prompt_guidance_against_prose(self):
        tool = TerminalBatchTool.create()[0]
        assert "summary" in tool.prompt_guidance
        assert "never" in tool.prompt_guidance

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

    def test_a_raising_command_is_isolated_to_its_entry(self, bound_tool):
        real, calls = bound_tool.executor._executor, []

        class _RaisingExecutor:
            def __call__(self, action, conversation=None):
                calls.append(action.command)
                if action.command == "boom":
                    raise RuntimeError("session died")
                return real(action, conversation)

        bound_tool.executor._executor = _RaisingExecutor()
        observation = bound_tool.executor(
            TerminalBatchAction(
                commands=["echo before", "boom", "echo after"], timeout=20
            )
        )

        assert calls == ["echo before", "boom", "echo after"]
        assert observation.exit_codes == [0, None, 0]
        assert observation.failed_count == 1
        assert observation.is_error is True
        assert "before" in observation.results[0]
        assert "raised RuntimeError: session died" in observation.results[1]
        assert "after" in observation.results[2]

    def test_a_raising_command_stops_the_batch_when_asked(self, bound_tool):
        real = bound_tool.executor._executor

        class _RaisingExecutor:
            def __call__(self, action, conversation=None):
                if action.command == "boom":
                    raise RuntimeError("session died")
                return real(action, conversation)

        bound_tool.executor._executor = _RaisingExecutor()
        observation = bound_tool.executor(
            TerminalBatchAction(
                commands=["boom", "echo after"], stop_on_failure=True, timeout=20
            )
        )
        assert observation.exit_codes == [None, None]
        assert "skipped" in observation.results[1]


class TestResolution:
    def test_terminal_batch_resolves_through_tool_resolution(self):
        resolved = resolve_toolset(["terminal", "terminal_batch"], "context")
        assert [t.name for t in resolved.sdk_tools] == [
            "terminal",
            "terminal_batch",
        ]
        assert resolved.has("terminal_batch")
        assert resolved.dispatch_tool_enabled is False

    def test_registered_under_its_own_name(self):
        from openhands.sdk.tool.registry import list_registered_tools

        resolve_toolset(["terminal_batch"], "context")
        assert "terminal_batch" in list_registered_tools()
