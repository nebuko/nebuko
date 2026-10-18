"""Resolve configured tool names into SDK tool specs + builtin tool names.

The single owner of the sdk-vs-builtin decision for an agent's tool list
(issue #860). Before this module existed the loop lived inline in
``run_agent.py`` and silently relied on a hidden import side effect: the old
``from openhands.tools.preset.default import get_default_condenser`` import
pulled in the ``openhands.tools`` package, whose ``__init__`` registers
``terminal`` / ``file_editor`` / ``task_tracker`` with the SDK registry at
import time. Commit 268bd6e swapped that import for ``build_condenser`` (which
only touches ``openhands.sdk``), dropping the registration while the loop kept
emitting ``Tool(name="terminal")`` — so the SDK raised
``KeyError: "ToolDefinition 'terminal' is not registered"`` at first
``send_message``.

Registration is therefore made explicit and deterministic here via
:func:`ensure_sdk_tools_registered`; the tool specs are emitted only after the
names are resolvable.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from openhands.sdk import get_logger
from openhands.sdk.tool.registry import list_registered_tools, register_tool
from openhands.sdk.tool.spec import Tool

logger = get_logger(__name__)

#: Config tool names the SDK resolves from its own registry (as opposed to the
#: SDK's builtin classes handled by ``include_default_tools``).
_SDK_TOOL_NAMES = ("terminal", "file_editor", "task_tracker")

#: Config tool name -> SDK builtin class name.
_BUILTIN_TOOL_MAP = {"think": "ThinkTool", "finish": "FinishTool"}

_TERMINAL_BATCH_TOOL_NAME = "TerminalBatchTool"
_DISPATCH_TOOL_NAME = "DispatchNextTool"
_CALL_AGENT_TOOL_NAME = "CallAgentTool"
_FINALIZE_TOOL_NAME = "FinalizeTool"
_SCAFFOLD_BRANCH_TOOL_NAME = "ScaffoldBranchReportTool"
_COMPLEXITY_REVIEW_TOOL_NAME = "ComplexityReviewTool"
_HANDOFF_TOOL_NAME = "HandoffTool"


@dataclass(frozen=True)
class ResolvedToolset:
    """Immutable result of resolving configured tool names.

    ``sdk_tools`` carry names already registered with the SDK registry;
    ``builtin_tools`` are class names handed to ``include_default_tools``.
    """

    sdk_tools: tuple[Tool, ...]
    builtin_tools: tuple[str, ...]
    dispatch_tool_enabled: bool

    def has(self, tool_name: str) -> bool:
        """Whether ``tool_name`` is among the emitted SDK or builtin names."""
        if tool_name in self.builtin_tools:
            return True
        return any(tool.name == tool_name for tool in self.sdk_tools)


def ensure_sdk_tools_registered() -> None:
    """Idempotently register TerminalTool, FileEditorTool, TaskTrackerTool.

    Deterministic — importing the classes from ``openhands.tools`` triggers the
    SDK's own import-time registration, but this function *also* registers any
    name that is still missing, so a future change to those side effects cannot
    silently reintroduce the #860 regression. Existing names are left alone to
    avoid ``register_tool``'s duplicate-registration warning.
    """
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    _register_if_missing(TerminalTool)
    _register_if_missing(FileEditorTool)
    _register_if_missing(TaskTrackerTool)


def _register_if_missing(tool_class) -> None:
    """Register ``tool_class`` under its own ``.name`` unless already present."""
    if tool_class.name in list_registered_tools():
        return
    register_tool(tool_class.name, tool_class)


# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_terminal_batch_tool_registered = False


def _ensure_terminal_batch_tool_registered() -> None:
    """Register the ``terminal_batch`` ToolDefinition with the SDK registry.

    The engine-authored wrapper around the SDK's ``terminal`` tool: it takes a
    list of single statements and replays them on one persistent session, so a
    multi-step explore costs one model turn instead of one per command
    (issue #982). Idempotent — safe to call on every agent run.
    """
    global _terminal_batch_tool_registered
    if _terminal_batch_tool_registered:
        return
    from agent_script.features.terminal_batch_tool.terminal_batch_tool import (
        TerminalBatchTool,
    )

    register_tool(TerminalBatchTool.name, TerminalBatchTool)
    _terminal_batch_tool_registered = True


# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_dispatch_tool_registered = False


def _ensure_dispatch_tool_registered() -> None:
    """Register the ``dispatch_next`` ToolDefinition with the SDK registry.

    Idempotent — safe to call on every agent run.
    """
    global _dispatch_tool_registered
    if _dispatch_tool_registered:
        return
    from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
        DispatchNextTool,
    )

    register_tool(_DISPATCH_TOOL_NAME, DispatchNextTool)
    _dispatch_tool_registered = True


# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_call_agent_tool_registered = False


def _ensure_call_agent_tool_registered() -> None:
    """Register the ``call_agent`` ToolDefinition with the SDK registry.

    Idempotent — safe to call on every agent run.
    """
    global _call_agent_tool_registered
    if _call_agent_tool_registered:
        return
    from agent_script.features.call_agent_tool.call_agent_tool import (
        CallAgentTool,
    )

    register_tool(_CALL_AGENT_TOOL_NAME, CallAgentTool)
    _call_agent_tool_registered = True


# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_finalize_tool_registered = False


def _ensure_finalize_tool_registered() -> None:
    """Register the ``finalize`` ToolDefinition with the SDK registry.

    Idempotent — safe to call on every agent run.
    """
    global _finalize_tool_registered
    if _finalize_tool_registered:
        return
    from agent_script.features.finalize_tool.finalize_tool import FinalizeTool

    register_tool(_FINALIZE_TOOL_NAME, FinalizeTool)
    _finalize_tool_registered = True


# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_scaffold_branch_tool_registered = False


def _ensure_scaffold_branch_tool_registered() -> None:
    """Register the ``report_scaffold_branch`` ToolDefinition with the registry.

    Idempotent — safe to call on every agent run.
    """
    global _scaffold_branch_tool_registered
    if _scaffold_branch_tool_registered:
        return
    from agent_script.features.scaffold_branch_tool.scaffold_branch_tool import (
        ScaffoldBranchReportTool,
    )

    register_tool(_SCAFFOLD_BRANCH_TOOL_NAME, ScaffoldBranchReportTool)
    _scaffold_branch_tool_registered = True


# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_complexity_review_tool_registered = False


def _ensure_complexity_review_tool_registered() -> None:
    """Register the ``complexity_review`` ToolDefinition with the registry.

    Idempotent — safe to call on every agent run.
    """
    global _complexity_review_tool_registered
    if _complexity_review_tool_registered:
        return
    from agent_script.features.complexity_review_tool.complexity_review_tool import (
        ComplexityReviewTool,
    )

    register_tool(_COMPLEXITY_REVIEW_TOOL_NAME, ComplexityReviewTool)
    _complexity_review_tool_registered = True


# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_handoff_tool_registered = False


def _ensure_handoff_tool_registered() -> None:
    """Register the ``handoff`` ToolDefinition with the SDK registry.

    Idempotent — safe to call on every agent run.
    """
    global _handoff_tool_registered
    if _handoff_tool_registered:
        return
    from agent_script.features.handoff_tool.handoff_tool import HandoffTool

    register_tool(_HANDOFF_TOOL_NAME, HandoffTool)
    _handoff_tool_registered = True


def resolve_toolset(tool_names: Iterable[str], role_name: str) -> ResolvedToolset:
    """Map configured tool names to SDK/builtin specs.

    Registers the SDK-provided tools (terminal, file_editor, task_tracker)
    explicitly before emitting their ``Tool`` specs, so the SDK can resolve them
    at first ``send_message`` regardless of import order.
    """
    requested = list(tool_names)
    if any(name in _SDK_TOOL_NAMES for name in requested):
        ensure_sdk_tools_registered()
    # ``terminal_batch`` replays its commands through the ``terminal`` tool's
    # session, so configuring it without ``terminal`` would be a silent no-op
    # at call time. Warn instead of emitting a tool that cannot run.
    if "terminal_batch" in requested and "terminal" not in requested:
        logger.warning(
            f"'terminal_batch' requires 'terminal' for agent '{role_name}' "
            "— skipping the batch tool"
        )
        requested = [name for name in requested if name != "terminal_batch"]

    sdk_tools: list[Tool] = []
    builtin_tools: list[str] = []
    dispatch_tool_enabled = False

    for tool_name in requested:
        if tool_name in _SDK_TOOL_NAMES:
            sdk_tools.append(Tool(name=tool_name))
        elif tool_name in _BUILTIN_TOOL_MAP:
            builtin_tools.append(_BUILTIN_TOOL_MAP[tool_name])
        elif tool_name == "terminal_batch":
            _ensure_terminal_batch_tool_registered()
            sdk_tools.append(Tool(name=_TERMINAL_BATCH_TOOL_NAME))
        elif tool_name == "dispatch_next":
            _ensure_dispatch_tool_registered()
            sdk_tools.append(Tool(name=_DISPATCH_TOOL_NAME))
            dispatch_tool_enabled = True
        elif tool_name == "call_agent":
            _ensure_call_agent_tool_registered()
            sdk_tools.append(Tool(name=_CALL_AGENT_TOOL_NAME))
        elif tool_name == "finalize":
            _ensure_finalize_tool_registered()
            sdk_tools.append(Tool(name=_FINALIZE_TOOL_NAME))
        elif tool_name == "report_scaffold_branch":
            _ensure_scaffold_branch_tool_registered()
            sdk_tools.append(Tool(name=_SCAFFOLD_BRANCH_TOOL_NAME))
        elif tool_name == "complexity_review":
            _ensure_complexity_review_tool_registered()
            sdk_tools.append(Tool(name=_COMPLEXITY_REVIEW_TOOL_NAME))
        elif tool_name == "handoff":
            _ensure_handoff_tool_registered()
            sdk_tools.append(Tool(name=_HANDOFF_TOOL_NAME))
        else:
            logger.warning(
                f"Unknown tool '{tool_name}' for agent '{role_name}' — skipping"
            )

    return ResolvedToolset(
        sdk_tools=tuple(sdk_tools),
        builtin_tools=tuple(builtin_tools),
        dispatch_tool_enabled=dispatch_tool_enabled,
    )
