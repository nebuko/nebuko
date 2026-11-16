"""Claude Code harness — the official SDK behind the port (issue #1368).

``claude-agent-sdk`` drives the plain ``claude`` binary in headless mode and can
host an **in-process** MCP server, which is what lets ``finalize`` (and the other
engine tools) reach Claude Code with a schema and without a sidecar.

The SDK's client is async; :class:`ClaudeCodeSession` bridges it to the
synchronous :class:`~nebuko.engine.harness.base.HarnessSession` protocol with a
daemon thread, a private event loop and a queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import uuid
from types import SimpleNamespace
from typing import Any, Callable, Optional

from nebuko.engine.finalize import OutputContract

from .base import (
    BuiltAgent,
    ClosingResult,
    HarnessCapabilities,
    HarnessEvent,
    drive_turn,
)
from .engine_tools import MCP_SERVER_NAME, TOOL_PREFIX, EngineToolServer, FinalizeSink
from .mcp import resolve_mcp_env

__all__ = [
    "ClaudeCodeClosingChannel",
    "ClaudeCodeConversation",
    "ClaudeCodeHarness",
    "ClaudeCodeSession",
]

log = logging.getLogger(__name__)

#: The only tool the closing turn may call.
FINALIZE_TOOL = f"{TOOL_PREFIX}finalize"

#: Engine tool name -> the Claude Code built-in that serves the same role.
#: A name missing here (``think``, ``finish``) has no Claude Code equivalent and
#: is served by the engine MCP server only when it is an engine tool.
BUILTIN_TOOLS: dict[str, str] = {
    "terminal": "Bash",
    "read": "Read",
    "edit": "Edit",
    "write": "Write",
    "file_editor": "Edit",
    "task_tracker": "TodoWrite",
}

#: Engine tools the in-process MCP server exposes.
ENGINE_TOOL_NAMES = frozenset(
    {
        "finalize",
        "terminal_batch",
        "complexity_review",
        "fetch_comment_bodies",
        "report_scaffold_branch",
    }
)

CLOSING_PROMPT = (
    "Engine finalize turn - the work phase is over; do not call any work tool. "
    "Call the `finalize` tool once. Put your COMPLETE, self-contained report in "
    "its `report` argument (never a one-line note) and your turn outcome in "
    "`turn_outcome`.\n\n"
    "{sections}\n"
    "Set `turn_outcome.appreciation` ('approved' | 'rejected' | 'hold') and "
    "`turn_outcome.hold_kind` = 'awaiting_input' with a hold. Where the "
    "workflow goes next is engine topology, never your choice."
)


class _ToolPolicy:
    """The session's current tool allow-list, shared with the SDK hook."""

    def __init__(self) -> None:
        self._allow: frozenset[str] | None = None
        self._lock = threading.Lock()

    def set(self, allow: frozenset[str] | None) -> None:
        with self._lock:
            self._allow = allow

    def permits(self, tool_name: str) -> bool:
        with self._lock:
            allow = self._allow
        return allow is None or tool_name in allow


class ClaudeCodeSession:
    """A live ``ClaudeSDKClient`` behind the :class:`HarnessSession` protocol."""

    def __init__(
        self, options: Any, sink: FinalizeSink, policy: "_ToolPolicy | None" = None
    ) -> None:
        self.options = options
        self.sink = sink
        self.policy = policy or _ToolPolicy()
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._callbacks: list[Callable[[HarnessEvent], None]] = []

    # ── HarnessSession ───────────────────────────────────────────
    def send_message(self, text: str) -> None:
        self._ensure_client()
        self._submit(self._client.query(text))

    def run(self, timeout_s: float | None = None) -> None:
        self._ensure_client()
        try:
            self._submit(self._receive(), timeout=timeout_s)
        except TimeoutError:
            log.warning("[phase:agent] claude turn exceeded its budget - interrupting")
            self.interrupt()
            raise

    def interrupt(self) -> None:
        """Stop the in-flight turn, keeping the session usable for a wind-down."""
        if self._client is None:
            return
        with contextlib.suppress(Exception):
            self._submit(self._client.interrupt(), timeout=30.0)

    def close(self) -> None:
        client, loop = self._client, self._loop
        if client is None or loop is None:
            return
        try:
            self._submit(client.disconnect(), timeout=30.0)
        except Exception as exc:  # noqa: BLE001 - close must never raise
            log.warning("[phase:agent] claude session close failed: %s", exc)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(10.0)
        self._client = None

    def on_event(self, callback: Callable[[HarnessEvent], None]) -> None:
        self._callbacks.append(callback)

    def set_tool_policy(self, allow: frozenset[str] | None) -> None:
        """Restrict the tools this session may call (``None`` = all)."""
        self.policy.set(allow)

    # ── the run path's soft-timeout contract ─────────────────────
    # ``run_agent`` interrupts rather than closes on a soft timeout so the
    # wind-down turn still has a live session.

    def permits(self, tool_name: str) -> bool:
        """True when ``tool_name`` is allowed by the current policy."""
        return self.policy.permits(tool_name)

    # ── async bridge ─────────────────────────────────────────────
    def _ensure_client(self) -> None:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever, daemon=True, name="claude-sdk-loop"
            )
            self._thread.start()
        if self._client is not None:
            return
        from claude_agent_sdk import ClaudeSDKClient  # noqa: PLC0415

        self._client = ClaudeSDKClient(self.options)
        self._submit(self._client.connect())

    def _submit(self, coro: Any, timeout: float | None = None) -> Any:
        """Run ``coro`` on the private loop and wait for its result."""
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)

    async def _receive(self) -> None:
        """Consume one turn's messages, emitting normalised events."""
        async for message in self._client.receive_response():
            for event in _events_of(message):
                self._emit(event)

    def _emit(self, event: HarnessEvent) -> None:
        for callback in self._callbacks:
            callback(event)


def _events_of(message: Any) -> list[HarnessEvent]:
    """Normalise one SDK message into zero or more :class:`HarnessEvent`s."""
    from claude_agent_sdk import (  # noqa: PLC0415
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    events: list[HarnessEvent] = []
    if isinstance(message, AssistantMessage):
        for block in message.content or []:
            if isinstance(block, TextBlock):
                events.append(HarnessEvent(kind="commentary", text=block.text))
            elif isinstance(block, ToolUseBlock):
                events.append(
                    HarnessEvent(
                        kind="action", text=block.name, data={"input": block.input}
                    )
                )
    elif isinstance(message, UserMessage):
        # Tool results arrive on the user side of the stream, not the assistant's.
        content = message.content if isinstance(message.content, list) else []
        for block in content:
            if isinstance(block, ToolResultBlock):
                events.append(
                    HarnessEvent(
                        kind="observation",
                        text=_block_text(block.content),
                        data={"is_error": bool(block.is_error)},
                    )
                )
    elif isinstance(message, ResultMessage):
        events.append(
            HarnessEvent(
                kind="error" if message.is_error else "result",
                text=message.result or "",
                data={"session_id": getattr(message, "session_id", "")},
            )
        )
    return events


class ClaudeCodeClosingChannel:
    """Elicit the closing result from the in-process ``finalize`` tool."""

    def elicit(
        self, session: Any, contract: OutputContract | None
    ) -> ClosingResult:
        sink: FinalizeSink = session.sink
        sink.reset()
        sink.require_report = True
        prompt = CLOSING_PROMPT.format(
            sections=contract.prompt_block() if contract is not None else ""
        )
        session.set_tool_policy(frozenset({FINALIZE_TOOL}))
        try:
            drive_turn(session, prompt)
        except Exception as error:  # noqa: BLE001 - degrade, never abort
            log.warning("[phase:agent] claude finalize turn failed: %s", error)
            return ClosingResult()
        finally:
            session.set_tool_policy(None)
            sink.require_report = False
        return sink.result() or ClosingResult()


class ClaudeCodeConversation:
    """Conversation-shaped view of a Claude Code run for the legacy assembly.

    ``run_agent`` returns an OpenHands ``Conversation`` to the assembly, which
    reads ``id``, ``state.events``, ``execution_status`` and ``run_outcome``.
    This adapter exposes the same read-only surface over a harness session, so
    metrics, answer capture and ``build_output`` run unchanged. The session and
    its closing channel ride along for the host's closing turn (issue #1368).
    """

    def __init__(
        self,
        *,
        session: "ClaudeCodeSession",
        closing_channel: "ClaudeCodeClosingChannel",
        workspace: str,
        run_id: str | None = None,
    ) -> None:
        self.id = run_id or uuid.uuid4().hex
        self.harness_session = session
        self.harness_closing_channel = closing_channel
        self.state = SimpleNamespace(
            events=[],
            workspace=SimpleNamespace(working_dir=workspace),
            env_observation_persistence_dir=None,
        )
        self.execution_status = SimpleNamespace(value="finished")
        self.run_outcome = None
        session.on_event(self._record)

    def _record(self, event: HarnessEvent) -> None:
        """Mirror agent text turns into the legacy event stream."""
        if event.kind not in ("commentary", "result") or not event.text:
            return
        from openhands.sdk.event import MessageEvent  # noqa: PLC0415
        from openhands.sdk.llm.message import Message, TextContent  # noqa: PLC0415

        self.state.events.append(
            MessageEvent(
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text=event.text)]
                ),
            )
        )


class ClaudeCodeHarness:
    """Claude Code driven through ``claude-agent-sdk``."""

    name = "claude-code"

    def capabilities(self) -> HarnessCapabilities:
        return HarnessCapabilities(
            backend=self.name,
            native_tools=False,
            response_schema=True,
            engine_tools="mcp",
            mcp="full",
            tool_narrowing=True,
            condenser=False,
            # The SDK's ``skills`` catalog is not wired here: a discover-mode
            # skill on this backend is a config error, not a silent drop.
            skills_discover=False,
            # The run path implements the bounded missing-outcome recall for
            # harness sessions too: an empty sink after the work turn is the
            # same "no decision" signal the OpenHands path reads.
            dispatch_recall=True,
            publication_guard=False,
            closing_channel="tool",
        )

    def build_agent(
        self,
        *,
        role_name: str,
        agent_cfg: Any,
        prompt: str,
        agent_context: Any,
    ) -> BuiltAgent:
        from claude_agent_sdk import ClaudeAgentOptions  # noqa: PLC0415

        sink = FinalizeSink()
        server = EngineToolServer(
            role_name=role_name,
            agent_cfg=agent_cfg,
            agent_context=agent_context,
            sink=sink,
        )
        policy = _ToolPolicy()
        builtins, engine_tools = _declared_tools(agent_cfg)
        mcp_servers = {MCP_SERVER_NAME: server.as_sdk_server()}
        mcp_servers.update(_stdio_servers(agent_cfg))
        options = ClaudeAgentOptions(
            system_prompt=prompt,
            mcp_servers=mcp_servers,
            tools=builtins,
            # Headless Claude Code denies every tool outside ``allowed_tools``.
            allowed_tools=[*builtins, *engine_tools],
            hooks={"PreToolUse": [_policy_hook(policy)]},
            model=_model_alias(agent_cfg),
            cwd=os.environ.get("GFA_REPO_ROOT") or os.getcwd(),
            permission_mode="acceptEdits",
        )
        return BuiltAgent(
            session=ClaudeCodeSession(options, sink, policy), prompt=prompt
        )

    def build_conversation(
        self, *, role_name: str, agent_cfg: Any, prompt: str, agent_context: Any
    ) -> "ClaudeCodeConversation":
        """Build the agent and wrap it in the assembly's conversation view."""
        built = self.build_agent(
            role_name=role_name,
            agent_cfg=agent_cfg,
            prompt=prompt,
            agent_context=agent_context,
        )
        return ClaudeCodeConversation(
            session=built.session,
            closing_channel=self.make_closing_channel(),
            workspace=str(getattr(agent_context, "workspace", "") or os.getcwd()),
            run_id=getattr(agent_context, "id", None),
        )

    def make_closing_channel(self) -> ClaudeCodeClosingChannel:
        return ClaudeCodeClosingChannel()


def _declared_tools(agent_cfg: Any) -> tuple[list[str], list[str]]:
    """Split a role's declared tools into Claude built-ins and engine MCP tools."""
    declared = getattr(agent_cfg, "tools", None) or ()
    builtins: list[str] = []
    engine_tools: list[str] = []
    for name in declared:
        if name in ENGINE_TOOL_NAMES:
            engine_tools.append(f"{TOOL_PREFIX}{name}")
        elif name in BUILTIN_TOOLS:
            builtin = BUILTIN_TOOLS[name]
            if builtin not in builtins:
                builtins.append(builtin)
    # Every step ends through ``finalize``; it must be callable even when the
    # role's static tool list predates it (mirrors the OpenHands run path).
    if FINALIZE_TOOL not in engine_tools:
        engine_tools.append(FINALIZE_TOOL)
    return builtins, engine_tools


def _stdio_servers(agent_cfg: Any) -> dict[str, Any]:
    """The role's declared MCP servers as SDK stdio configs."""
    servers: dict[str, Any] = {}
    for spec in getattr(agent_cfg, "mcp_servers", None) or ():
        env, missing = resolve_mcp_env(spec.env)
        if missing:
            log.warning(
                f"MCP server '{spec.name}' skipped — unset env var(s): "
                f"{', '.join(missing)}"
            )
            continue
        servers[spec.name] = {
            "type": "stdio",
            "command": spec.command,
            "args": list(spec.args),
            "env": env,
        }
    return servers


def _policy_hook(policy: "_ToolPolicy") -> Any:
    """A ``PreToolUse`` hook enforcing ``policy`` on every tool call.

    ``can_use_tool`` is shadowed by ``allowed_tools`` / ``permission_mode``, so
    it cannot narrow a session that already allows a tool; a ``PreToolUse`` hook
    sees every call and can deny it.
    """
    from claude_agent_sdk import HookMatcher  # noqa: PLC0415

    async def _guard(input_data: Any, tool_use_id: Any, context: Any) -> Any:
        name = str(input_data.get("tool_name", ""))
        if policy.permits(name):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"tool '{name}' is not allowed during this turn"
                ),
            }
        }

    return HookMatcher(matcher=None, hooks=[_guard])


def _block_text(content: Any) -> str:
    """Flatten a tool result's content (string or block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
        )
    return "" if content is None else str(content)


def _model_alias(agent_cfg: Any) -> Optional[str]:
    """The Claude Code model alias from the resolved ``model_path``."""
    model = getattr(agent_cfg, "model_ref", None)
    path = getattr(model, "model_path", "") or ""
    return path.rsplit("/", 1)[-1] or None
