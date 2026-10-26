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
from typing import TYPE_CHECKING

from openhands.sdk import get_logger
from openhands.sdk.tool.registry import list_registered_tools, register_tool
from openhands.sdk.tool.spec import Tool

if TYPE_CHECKING:
    from config.sub_domains.token_budget.token_budget import TokenBudgetPolicy

logger = get_logger(__name__)

#: Config tool names the SDK resolves from its own registry (as opposed to the
#: SDK's builtin classes handled by ``include_default_tools``).
# ``read`` / ``edit`` / ``write`` are nebuko-owned (issue #1118) but resolve
# from the same SDK registry, so they live here too.
_SDK_TOOL_NAMES = ("terminal", "file_editor", "task_tracker", "read", "edit", "write")

#: Config tool name -> SDK builtin class name.
_BUILTIN_TOOL_MAP = {"think": "ThinkTool", "finish": "FinishTool"}

#: The name ``TerminalBatchTool`` auto-derives from its class name, and the
#: name the SDK registry is keyed by — the two must stay in step, because
#: ``resolve_tool`` looks the emitted ``Tool`` spec up in that registry.
_TERMINAL_BATCH_TOOL_NAME = "terminal_batch"

#: Config tool name the engine owns but resolves through the SDK registry.
_FETCH_COMMENT_BODIES_TOOL_NAME = "FetchCommentBodiesTool"

_FINALIZE_TOOL_NAME = "FinalizeTool"
_SCAFFOLD_BRANCH_TOOL_NAME = "ScaffoldBranchReportTool"
_COMPLEXITY_REVIEW_TOOL_NAME = "ComplexityReviewTool"
_HANDOFF_TOOL_NAME = "HandoffTool"

#: Canonical ``tools:`` entry names this module can resolve (the config-facing
#: side of the sdk-vs-builtin decision). Single source of truth for the offline
#: linter's C3 rule: ``config_lint.KNOWN_TOOLS`` derives from this so the two
#: lists can never drift apart again (issue #748 follow-up).
CONFIG_TOOL_NAMES: frozenset[str] = frozenset(
    {
        *_SDK_TOOL_NAMES,
        "terminal_batch",
        *_BUILTIN_TOOL_MAP,
        "finalize",
        "report_scaffold_branch",
        "complexity_review",
        "handoff",
        "fetch_comment_bodies",
    }
)


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
    """Idempotently register the environment + nebuko file tools.

    Deterministic — importing the SDK classes from ``openhands.tools`` triggers
    the SDK's own import-time registration, but this function *also* registers
    any name that is still missing, so a future change to those side effects
    cannot silently reintroduce the #860 regression. Existing names are left
    alone to avoid ``register_tool``'s duplicate-registration warning — except
    ``file_editor``, which is deliberately re-registered with the engine's
    ``BoundedFileEditorTool`` subclass (same registered name, so configs keep
    resolving; it adds view-range prompt guidance and caps unbounded views —
    the GLM-5.3-flash run of issue #1114 paid ~16K context characters per
    whole-file view). The nebuko ``read``/``edit``/``write`` tools
    (issue #1118) have no SDK import side effect, so their registration lives
    entirely here.
    """
    from agent_script.features.bounded_file_editor.bounded_file_editor import (
        BoundedFileEditorTool,
    )
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    from agent_script.sub_domains.tool_resolution.file_tools import (
        EditTool,
        ReadTool,
        WriteTool,
    )

    _register_if_missing(TerminalTool)
    _register_bounded_file_editor(BoundedFileEditorTool, FileEditorTool)
    _register_if_missing(TaskTrackerTool)
    _register_if_missing(ReadTool)
    _register_if_missing(EditTool)
    _register_if_missing(WriteTool)


def _register_if_missing(tool_class) -> None:
    """Register ``tool_class`` under its own ``.name`` unless already present."""
    if tool_class.name in list_registered_tools():
        return
    register_tool(tool_class.name, tool_class)


# Module-level guard: re-registering file_editor warns once per process on the
# SDK's side (its import already claimed the name), so do it only once here.
_bounded_file_editor_registered = False


def _register_bounded_file_editor(bounded_class, sdk_class) -> None:
    """Register the engine's ``file_editor`` subclass over the SDK's entry.

    ``register_tool`` overwrites the registry slot for a name, so the order in
    the process decides which class answers ``Tool(name="file_editor")`` —
    this must therefore run after ``openhands.tools``'s import-time
    registration, which is why it lives here and not in the module's import
    side effects.
    """
    global _bounded_file_editor_registered
    if _bounded_file_editor_registered:
        return
    # The SDK claimed the name at import time; clear its entry first so the
    # replacement registers without a duplicate-name warning.
    from openhands.sdk.tool import registry

    with registry._LOCK:
        registry._REG.pop(bounded_class.name, None)
        registry._USABILITY_REG.pop(bounded_class.name, None)
        registry._TOOL_CLASSES.pop(bounded_class.name, None)
    register_tool(bounded_class.name, bounded_class)
    _bounded_file_editor_registered = True


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

# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_fetch_comment_bodies_tool_registered = False


def _ensure_fetch_comment_bodies_tool_registered(
    context_bodies: dict[str, str] | None = None,
) -> None:
    """Register the ``fetch_comment_bodies`` ToolDefinition with the registry.

    Idempotent — safe to call on every agent run. The run's cached comment
    bodies are injected at registration time so the executor's lookups read
    from the same cache the prompt summaries were built from.
    """
    global _fetch_comment_bodies_tool_registered
    if _fetch_comment_bodies_tool_registered:
        return
    from functools import partial

    from nebuko.engine.fetch_comment_bodies_tool import FetchCommentBodiesTool

    # register_tool only accepts a ToolDefinition subclass whose create() takes
    # **params; bind the run's cached bodies through a small subclass instead.
    tool_cls = type(
        "FetchCommentBodiesTool",
        (FetchCommentBodiesTool,),
        {"_context_bodies": context_bodies or {}},
    )
    register_tool(_FETCH_COMMENT_BODIES_TOOL_NAME, tool_cls)
    _fetch_comment_bodies_tool_registered = True


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


def resolve_toolset(
    tool_names: Iterable[str],
    role_name: str,
    token_budget: "TokenBudgetPolicy | None" = None,
    context_bodies: "dict[str, str] | None" = None,
) -> ResolvedToolset:
    """Map configured tool names to SDK/builtin specs.

    Registers the SDK-provided tools (terminal, file_editor, task_tracker) and
    the nebuko file tools (read, edit, write — issue #1118) explicitly before
    emitting their ``Tool`` specs, so the SDK can resolve them at first
    ``send_message`` regardless of import order.

    ``token_budget`` (issue #851) carries the role's ``prompt_profile`` and
    ``require_batched_recon``, which only ``file_editor`` accepts.
    """
    requested = list(tool_names)
    profile_params = (
        {
            "prompt_profile": token_budget.prompt_profile,
            "require_batched_recon": token_budget.require_batched_recon,
        }
        if token_budget is not None
        else {}
    )
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
            # Only file_editor accepts the lean profile; terminal and
            # task_tracker take no create() parameters at all.
            if tool_name == "file_editor" and profile_params:
                sdk_tools.append(
                    Tool(
                        name=tool_name,
                        params={"prompt_profile": profile_params["prompt_profile"]},
                    )
                )
            else:
                sdk_tools.append(Tool(name=tool_name))
        elif tool_name in _BUILTIN_TOOL_MAP:
            builtin_tools.append(_BUILTIN_TOOL_MAP[tool_name])
        elif tool_name == "terminal_batch":
            _ensure_terminal_batch_tool_registered()
            sdk_tools.append(
                Tool(name=_TERMINAL_BATCH_TOOL_NAME, params=dict(profile_params))
            )
        elif tool_name in ("dispatch_next", "call_agent"):
            # Both tools were removed in issue #1033: every step ends through
            # ``finalize``, whose ``turn_outcome`` carries the verdict and whose
            # ``turn_outcome.agent_call`` carries the hand-off. Fail loudly so a
            # stale config is migrated rather than silently losing its route.
            raise ValueError(
                f"tool '{tool_name}' was removed: agent '{role_name}' must list "
                "'finalize' instead (the verdict travels in "
                "turn_outcome.appreciation and the hand-off in "
                "turn_outcome.agent_call)"
            )
        elif tool_name == "finalize":
            _ensure_finalize_tool_registered()
            sdk_tools.append(Tool(name=_FINALIZE_TOOL_NAME))
            # ``finalize`` is the terminal tool of every step (issue #1033), so
            # its presence is what enables the bounded missing-outcome recall.
            dispatch_tool_enabled = True
        elif tool_name == "report_scaffold_branch":
            _ensure_scaffold_branch_tool_registered()
            sdk_tools.append(Tool(name=_SCAFFOLD_BRANCH_TOOL_NAME))
        elif tool_name == "complexity_review":
            _ensure_complexity_review_tool_registered()
            sdk_tools.append(Tool(name=_COMPLEXITY_REVIEW_TOOL_NAME))
        elif tool_name == "handoff":
            _ensure_handoff_tool_registered()
            sdk_tools.append(Tool(name=_HANDOFF_TOOL_NAME))
        elif tool_name == "fetch_comment_bodies":
            _ensure_fetch_comment_bodies_tool_registered(
                context_bodies=context_bodies
            )
            sdk_tools.append(Tool(name=_FETCH_COMMENT_BODIES_TOOL_NAME))
        else:
            logger.warning(
                f"Unknown tool '{tool_name}' for agent '{role_name}' — skipping"
            )

    return ResolvedToolset(
        sdk_tools=tuple(sdk_tools),
        builtin_tools=tuple(builtin_tools),
        dispatch_tool_enabled=dispatch_tool_enabled,
    )
