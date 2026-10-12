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

from openhands.sdk.tool import Tool
from openhands.sdk.tool.registry import list_registered_tools

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

    def test_think_and_finish_map_to_builtins(self):
        resolved = resolve_toolset(["think", "finish"], "review")
        assert resolved.sdk_tools == ()
        assert resolved.builtin_tools == ("ThinkTool", "FinishTool")

    def test_dispatch_next_enables_dispatch(self):
        resolved = resolve_toolset(["dispatch_next"], "review")
        assert _names(resolved.sdk_tools) == ("DispatchNextTool",)
        assert resolved.dispatch_tool_enabled is True

    def test_call_agent_registers(self):
        resolved = resolve_toolset(["call_agent"], "architect")
        assert _names(resolved.sdk_tools) == ("CallAgentTool",)
        assert "CallAgentTool" in list_registered_tools()
        # A hand-off-only tool must not be mistaken for a dispatch tool.
        assert resolved.dispatch_tool_enabled is False

    def test_report_scaffold_branch_registers(self):
        resolved = resolve_toolset(["report_scaffold_branch"], "dev")
        assert _names(resolved.sdk_tools) == ("ScaffoldBranchReportTool",)
        assert resolved.dispatch_tool_enabled is False

    def test_engine_tools_resolve_via_sdk(self):
        resolved = resolve_toolset(
            ["dispatch_next", "report_scaffold_branch"], "dev"
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
            ["terminal", "think", "dispatch_next", "report_scaffold_branch"],
            "dev",
        )
        for name in ("terminal", "ThinkTool", "DispatchNextTool",
                     "ScaffoldBranchReportTool"):
            assert resolved.has(name)
        assert not resolved.has("finish")
        assert not resolved.has("file_editor")

    def test_default_tool_list_parity(self):
        """The legacy default list still resolves to the legacy toolset."""
        resolved = resolve_toolset(
            ["terminal", "file_editor", "task_tracker", "think", "finish"],
            "unknown",
        )
        assert _names(resolved.sdk_tools) == (
            "terminal",
            "file_editor",
            "task_tracker",
        )
        assert resolved.builtin_tools == ("ThinkTool", "FinishTool")
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
