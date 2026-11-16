"""Engine tools as an in-process MCP server (issue #1368).

Claude Code's only tool-injection surface is MCP, so the engine's control-plane
tools reach it through ``create_sdk_mcp_server``: the handlers are ordinary
Python functions in the engine process, called back over the SDK control
channel. No sidecar, no socket, no second process.

The handlers delegate to the same executors the OpenHands tools use — one
implementation, two transports, never a copy.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

from nebuko.engine.finalize import TurnOutcomePayload

from .base import ClosingResult

__all__ = ["EngineToolServer", "FinalizeSink", "MCP_SERVER_NAME", "TOOL_PREFIX"]

log = logging.getLogger(__name__)

#: The in-process server's name; tools surface as ``mcp__nebuko__<tool>``.
MCP_SERVER_NAME = "nebuko"
TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"


def _text(text: str, *, is_error: bool = False) -> dict[str, Any]:
    """An MCP tool result carrying one text block."""
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


class FinalizeSink:
    """Holds the closing turn's ``(report, turn_outcome)`` until the host reads it.

    ``finalize`` is a write-only sink: it needs no engine state mid-turn, so the
    handler can record and return immediately.
    """

    def __init__(self) -> None:
        self._report = ""
        self._outcome: Optional[TurnOutcomePayload] = None
        #: Set for the engine's closing turn, where a report is owed (#1038).
        self.require_report = False

    def reset(self) -> None:
        self._report = ""
        self._outcome = None

    def record(self, payload: dict[str, Any]) -> None:
        """Record one ``finalize`` call's arguments.

        Raises:
            ValueError: The ``turn_outcome`` is missing or invalid; the message
                is returned to the agent as a tool error.
        """
        outcome = TurnOutcomePayload.model_validate(payload.get("turn_outcome") or {})
        outcome.validate_runtime()
        report = str(payload.get("report") or "")
        if self.require_report and not report.strip():
            raise ValueError(
                "no report. The report is the `report` argument of finalize, "
                "and nothing is posted without it"
            )
        self._report = report
        self._outcome = outcome

    def result(self) -> Optional[ClosingResult]:
        """The recorded closing result, or ``None`` before any call."""
        if self._outcome is None:
            return None
        return ClosingResult(report=self._report, outcome=self._outcome)


class EngineToolServer:
    """The engine's control-plane tools as an in-process MCP server."""

    def __init__(
        self,
        *,
        role_name: str,
        agent_cfg: Any,
        agent_context: Any,
        sink: FinalizeSink,
    ) -> None:
        self.role_name = role_name
        self.agent_cfg = agent_cfg
        self.agent_context = agent_context
        self.sink = sink

    def handlers(self) -> list[Callable[..., Awaitable[dict[str, Any]]]]:
        """The async tool handlers, in declaration order."""
        return [
            self._finalize,
            self._terminal_batch,
            self._complexity_review,
            self._fetch_comment_bodies,
            self._report_scaffold_branch,
        ]

    # ── handlers ─────────────────────────────────────────────────
    async def _finalize(self, args: dict[str, Any]) -> dict[str, Any]:
        """Record the closing verdict; the report rides in ``report``."""
        try:
            self.sink.record(args)
        except Exception as exc:  # noqa: BLE001 - surface every violation
            return _text(
                f"finalize rejected: {exc}. Fix the turn_outcome and call "
                "finalize again.",
                is_error=True,
            )
        outcome = self.sink.result().outcome  # type: ignore[union-attr]
        return _text(
            f"Turn outcome recorded: appreciation={outcome.appreciation}, "
            f"hold_kind={outcome.hold_kind or '-'}."
        )

    async def _terminal_batch(self, args: dict[str, Any]) -> dict[str, Any]:
        """Run a batch of single-statement shell commands."""
        from nebuko.engine.lib.agent_script.features.terminal_batch_tool.terminal_batch_tool import (  # noqa: PLC0415
            TerminalBatchAction,
            TerminalBatchExecutor,
        )

        try:
            action = TerminalBatchAction.model_validate(args)
        except Exception as exc:  # noqa: BLE001
            return _text(f"terminal_batch rejected: {exc}", is_error=True)
        observation = TerminalBatchExecutor()(action, self._conversation())
        return _text(
            "\n\n".join(getattr(observation, "results", []) or [])
            or getattr(observation, "text", ""),
            is_error=bool(getattr(observation, "is_error", False)),
        )

    async def _complexity_review(self, args: dict[str, Any]) -> dict[str, Any]:
        """Record a complexity-tier review request for the governor."""
        from nebuko.engine.lib.agent_script.features.complexity_review_tool.complexity_review_tool import (  # noqa: PLC0415
            ComplexityReviewAction,
            ComplexityReviewExecutor,
        )

        try:
            action = ComplexityReviewAction.model_validate(args)
        except Exception as exc:  # noqa: BLE001
            return _text(f"complexity_review rejected: {exc}", is_error=True)
        observation = ComplexityReviewExecutor(self._run_id())(action)
        return _text(
            getattr(observation, "text", "") or "Complexity review recorded.",
            is_error=bool(getattr(observation, "is_error", False)),
        )

    async def _fetch_comment_bodies(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return the cached full bodies of the requested comment ids."""
        from nebuko.engine.fetch_comment_bodies_tool import (
            FetchCommentBodiesAction,
            FetchCommentBodiesExecutor,
        )

        try:
            action = FetchCommentBodiesAction(
                comment_ids=[int(cid) for cid in args.get("comment_ids") or []]
            )
        except Exception as exc:  # noqa: BLE001
            return _text(f"fetch_comment_bodies rejected: {exc}", is_error=True)
        result = FetchCommentBodiesExecutor(self._context_bodies()).execute(action)
        parts = [f"{cid}:\n{body}" for cid, body in result.bodies.items()]
        if result.not_found:
            parts.append("not found: " + ", ".join(str(i) for i in result.not_found))
        return _text("\n\n".join(parts) or "No comment ids given.")

    async def _report_scaffold_branch(self, args: dict[str, Any]) -> dict[str, Any]:
        """Record the pushed scaffold feature branch ref."""
        from nebuko.engine.lib.agent_script.features.scaffold_branch_tool.scaffold_branch_tool import (  # noqa: PLC0415
            ScaffoldBranchReportAction,
            ScaffoldBranchReportExecutor,
        )

        try:
            action = ScaffoldBranchReportAction.model_validate(args)
        except Exception as exc:  # noqa: BLE001
            return _text(f"report_scaffold_branch rejected: {exc}", is_error=True)
        observation = ScaffoldBranchReportExecutor(self._run_id())(action)
        return _text(
            getattr(observation, "text", "") or "Scaffold branch recorded.",
            is_error=bool(getattr(observation, "is_error", False)),
        )

    # ── helpers ──────────────────────────────────────────────────
    def _run_id(self) -> str:
        return str(getattr(self.agent_context, "id", "default"))

    def _context_bodies(self) -> dict[str, str]:
        bodies = getattr(self.agent_context, "context_bodies", None)
        return dict(bodies) if isinstance(bodies, dict) else {}

    def _conversation(self) -> Any:
        """A minimal conversation stand-in binding the shell to the workspace.

        A harness whose context carries no conversation (Claude Code) gets a
        stub bound to the run's workspace, so ``terminal_batch`` executes in
        the same checkout the agent works in.
        """
        conversation = getattr(self.agent_context, "conversation", None)
        if conversation is not None:
            return conversation
        workspace = getattr(self.agent_context, "workspace", None)
        if workspace is None:
            return None
        return SimpleNamespace(
            state=SimpleNamespace(
                workspace=SimpleNamespace(working_dir=str(workspace)),
                env_observation_persistence_dir=None,
            )
        )

    def as_sdk_server(self) -> Any:
        """Wrap the handlers as an in-process SDK MCP server."""
        from claude_agent_sdk import create_sdk_mcp_server, tool  # noqa: PLC0415

        specs = [
            (
                "finalize",
                "End this step: record the report and the typed turn_outcome.",
                {
                    "type": "object",
                    "properties": {
                        "report": {"type": "string"},
                        "turn_outcome": TurnOutcomePayload.model_json_schema(),
                    },
                    "required": ["report", "turn_outcome"],
                },
            ),
            (
                "terminal_batch",
                "Run several single-statement shell commands in order.",
                {"type": "object", "properties": {"commands": {"type": "array"}}},
            ),
            (
                "complexity_review",
                "Request a complexity-tier review from the governor.",
                {"type": "object", "properties": {}},
            ),
            (
                "fetch_comment_bodies",
                "Fetch the full bodies of cached thread comments by id.",
                {"type": "object", "properties": {}},
            ),
            (
                "report_scaffold_branch",
                "Report the pushed scaffold feature branch ref.",
                {"type": "object", "properties": {}},
            ),
        ]
        sdk_tools = [
            tool(name, description, schema)(handler)
            for (name, description, schema), handler in zip(
                specs, self.handlers(), strict=True
            )
        ]
        return create_sdk_mcp_server(name=MCP_SERVER_NAME, tools=sdk_tools)
