"""Tests for tool resolution + explicit SDK registration (issue #860).

The regression guard deliberately runs in a **fresh interpreter**: the bug was
masked whenever anything imported ``openhands.tools`` (whose package
``__init__`` registers the environment tools as an import side effect), so only
a process that never touched that package can prove ``resolve_toolset`` performs
the registration itself. This module therefore imports neither
``openhands.tools`` nor ``openhands.tools.preset``.
"""

import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
import pytest

from openhands.sdk.tool import Tool
from openhands.sdk.tool.registry import list_registered_tools, resolve_tool

from agent_script.sub_domains.tool_resolution.tool_resolution import (
    ResolvedToolset,
    ensure_sdk_tools_registered,
    resolve_toolset,
)

_TEST_FILE = Path(__file__).resolve()
_LIB_ROOT = _TEST_FILE.parents[3]  # src/nebuko/engine/lib
_SRC_ROOT = _TEST_FILE.parents[6]  # src


def _names(tools: tuple[Tool, ...]) -> tuple[str, ...]:
    return tuple(t.name for t in tools)


class TestCleanInterpreterRegression:
    """Reproduces the exact #860 failure mode in an untouched interpreter."""

    def test_sdk_tools_resolve_after_resolve_toolset(self):
        # The #860 failure was the SDK registry missing the environment tool
        # names; asserting registry membership is the direct check (resolving
        # an executor additionally needs a live workspace/agent, which belongs
        # to the engine, not this registration unit).
        script = textwrap.dedent(
            """
            from openhands.sdk.tool.registry import list_registered_tools

            from agent_script.sub_domains.tool_resolution.tool_resolution import (
                resolve_toolset,
            )

            # Precondition: nothing has imported openhands.tools yet, so the
            # registry is empty — the exact state that used to raise
            # KeyError: "ToolDefinition 'terminal' is not registered".
            assert "terminal" not in list_registered_tools(), "dirty interpreter"

            resolve_toolset(["terminal", "file_editor", "task_tracker"], "review")

            registered = list_registered_tools()
            for name in ("terminal", "file_editor", "task_tracker"):
                assert name in registered, name
            print("OK")
            """
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(_SRC_ROOT), str(_LIB_ROOT), env.get("PYTHONPATH", "")]
        )
        env["OPENHANDS_SUPPRESS_BANNER"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


class TestEnsureSdkToolsRegistered:
    def test_registers_all_environment_tools(self):
        ensure_sdk_tools_registered()
        registered = list_registered_tools()
        for name in ("terminal", "file_editor", "task_tracker"):
            assert name in registered

    def test_token_budget_flows_into_engine_tool_params(self):
        # Issue #851 S3: the role's token_budget knobs ride the Tool spec's
        # params into the engine-owned tools' create(); SDK tools take no
        # params and stay bare.
        from config.sub_domains.token_budget.token_budget import TokenBudgetPolicy

        policy = TokenBudgetPolicy(prompt_profile="lean", require_batched_recon=True)
        resolved = resolve_toolset(
            ["terminal", "terminal_batch", "file_editor", "task_tracker"],
            "dev",
            token_budget=policy,
        )
        params = {tool.name: tool.params for tool in resolved.sdk_tools}
        assert params["terminal_batch"] == {
            "prompt_profile": "lean",
            "require_batched_recon": True,
        }
        assert params["file_editor"] == {"prompt_profile": "lean"}
        assert params["terminal"] == {}
        assert params["task_tracker"] == {}

    def test_no_policy_emits_bare_specs(self):
        resolved = resolve_toolset(
            ["terminal", "terminal_batch", "file_editor"], "dev"
        )
        assert all(tool.params == {} for tool in resolved.sdk_tools)

    def test_default_policy_is_full_profile(self):
        from config.sub_domains.token_budget.token_budget import TokenBudgetPolicy

        resolved = resolve_toolset(
            ["terminal", "terminal_batch", "file_editor"],
            "dev",
            token_budget=TokenBudgetPolicy(),
        )
        params = {tool.name: tool.params for tool in resolved.sdk_tools}
        assert params["terminal_batch"] == {
            "prompt_profile": "full",
            "require_batched_recon": False,
        }
        assert params["file_editor"] == {"prompt_profile": "full"}

    def test_file_editor_resolves_to_the_bounded_subclass(self):
        from openhands.sdk.tool.registry import _TOOL_CLASSES

        from agent_script.features.bounded_file_editor.bounded_file_editor import (
            BoundedFileEditorTool,
        )

        ensure_sdk_tools_registered()
        assert _TOOL_CLASSES["file_editor"] is BoundedFileEditorTool

    def test_idempotent_and_warning_free(self, caplog):
        with caplog.at_level(logging.WARNING):
            ensure_sdk_tools_registered()
            ensure_sdk_tools_registered()
        assert "Duplicate tool name registered" not in caplog.text


class TestResolveToolset:
    def test_sdk_tools_emitted_and_resolvable(self):
        resolved = resolve_toolset(
            ["terminal", "file_editor", "task_tracker"], "review"
        )
        assert _names(resolved.sdk_tools) == (
            "terminal",
            "file_editor",
            "task_tracker",
        )
        assert resolved.builtin_tools == ()
        assert resolved.dispatch_tool_enabled is False
        registered = list_registered_tools()
        for name in ("terminal", "file_editor", "task_tracker"):
            assert name in registered

    def test_think_maps_to_the_think_builtin(self):
        resolved = resolve_toolset(["think"], "review")
        assert resolved.sdk_tools == ()
        assert resolved.builtin_tools == ("ThinkTool",)

    def test_finish_is_a_deprecated_alias_for_finalize(self, caplog):
        """Issue #1071: ``finish`` is no longer a tool, but a pre-existing
        .nebuko/ override that lists it still loads — resolved to the terminal
        ``finalize`` gate, with a deprecation warning."""
        with caplog.at_level(logging.WARNING):
            resolved = resolve_toolset(["finish"], "review")
        assert _names(resolved.sdk_tools) == ("FinalizeTool",)
        assert resolved.builtin_tools == ()
        assert resolved.dispatch_tool_enabled is True
        assert "'finish' is deprecated" in caplog.text

    def test_finalize_enables_dispatch(self):
        """Every step ends through ``finalize`` (issue #1033): the single
        terminal tool that carries both the verdict and any hand-off."""
        resolved = resolve_toolset(["finalize"], "review")
        assert _names(resolved.sdk_tools) == ("FinalizeTool",)
        assert resolved.dispatch_tool_enabled is True

    def test_the_removed_tools_are_a_migration_error(self):
        """``dispatch_next`` / ``call_agent`` are no longer agents' tools: a
        config still listing one fails loudly, pointing at ``finalize``."""
        for name in ("dispatch_next", "call_agent"):
            with pytest.raises(ValueError, match="was removed"):
                resolve_toolset([name], "architect")

    def test_report_scaffold_branch_registers(self):
        resolved = resolve_toolset(["report_scaffold_branch"], "dev")
        assert _names(resolved.sdk_tools) == ("ScaffoldBranchReportTool",)
        assert resolved.dispatch_tool_enabled is False

    def test_terminal_batch_registers_alongside_terminal(self):
        resolved = resolve_toolset(["terminal", "terminal_batch"], "context")
        assert _names(resolved.sdk_tools) == ("terminal", "terminal_batch")
        assert "terminal_batch" in list_registered_tools()
        assert resolved.dispatch_tool_enabled is False

    def test_terminal_batch_spec_is_resolvable_by_the_sdk(self):
        # The emitted Tool name must be the registry key, not the class name:
        # a mismatch only shows up at Agent init as a KeyError.
        resolved = resolve_toolset(["terminal", "terminal_batch"], "context")
        spec = next(t for t in resolved.sdk_tools if t.name == "terminal_batch")
        definitions = resolve_tool(spec, None)
        assert [d.name for d in definitions] == ["terminal_batch"]

    def test_terminal_batch_without_terminal_is_skipped_with_warning(self, caplog):
        # The batch tool runs on the terminal tool's session, so emitting it
        # alone would register a tool that cannot execute.
        with caplog.at_level(logging.WARNING):
            resolved = resolve_toolset(["terminal_batch"], "context")
        assert resolved.sdk_tools == ()
        assert "'terminal_batch' requires 'terminal'" in caplog.text

    def test_engine_tools_resolve_via_sdk(self):
        resolved = resolve_toolset(
            ["finalize", "report_scaffold_branch"], "dev"
        )
        registered = list_registered_tools()
        for name in _names(resolved.sdk_tools):
            assert name in registered

    def test_unknown_tool_is_skipped_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            resolved = resolve_toolset(["bogus"], "review")
        assert resolved.sdk_tools == ()
        assert resolved.builtin_tools == ()
        assert "Unknown tool 'bogus'" in caplog.text

    def test_has_covers_every_emitted_name(self):
        resolved = resolve_toolset(
            ["terminal", "think", "finalize", "report_scaffold_branch"],
            "dev",
        )
        for name in ("terminal", "ThinkTool", "FinalizeTool",
                     "ScaffoldBranchReportTool"):
            assert resolved.has(name)
        assert not resolved.has("finish")
        assert not resolved.has("file_editor")

    def test_default_tool_list_parity(self):
        """The default list resolves to the sdk tools plus ThinkTool; the
        terminal ``finalize`` gate is appended by run_agent (issue #1033)."""
        resolved = resolve_toolset(
            ["terminal", "file_editor", "task_tracker", "think"],
            "unknown",
        )
        assert _names(resolved.sdk_tools) == (
            "terminal",
            "file_editor",
            "task_tracker",
        )
        assert resolved.builtin_tools == ("ThinkTool",)
        assert resolved.dispatch_tool_enabled is False

    def test_resolved_toolset_is_frozen(self):
        resolved = resolve_toolset(["terminal"], "review")
        assert isinstance(resolved, ResolvedToolset)
        try:
            resolved.dispatch_tool_enabled = True
        except Exception:
            pass
        else:  # pragma: no cover - frozen dataclass must reject mutation
            raise AssertionError("ResolvedToolset must be immutable")
