"""Harness port — one seam for every agent runtime (issue #1368).

Two axes are independent here:

* **transport** — the :class:`HarnessAdapter` that runs one work turn on a
  given runtime (``openhands``, ``claude-code``, ...);
* **closing channel** — how that runtime hands the engine its closing
  ``(report, turn_outcome)`` result (a native tool, or parsed text).

Everything else is a *declared capability* the engine reads instead of
assuming, mirroring the ``PlatformAdapter`` / ``PosterCapabilities`` precedent.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any, Callable, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from nebuko.engine.agent_runner import EngineAgentUnavailable
from nebuko.engine.finalize import OutputContract, TurnOutcomePayload

__all__ = [
    "BuiltAgent",
    "ClosingChannel",
    "ClosingResult",
    "HarnessAdapter",
    "HarnessCapabilities",
    "HarnessEvent",
    "HarnessSession",
    "drive_turn",
    "register_harness",
    "registered_harnesses",
    "resolve_harness",
]

log = logging.getLogger(__name__)

#: Watchdog budget for one engine-elicited closing turn (seconds).
CLOSING_TURN_TIMEOUT_S = 240.0


class HarnessCapabilities(BaseModel):
    """What one harness can serve, read by the engine instead of assumed."""

    model_config = ConfigDict(frozen=True)

    backend: str
    #: In-process host-defined tool registry (OpenHands).
    native_tools: bool = False
    #: Tool-argument JSON-schema enforcement.
    response_schema: bool = False
    #: How the engine's own tools reach the model.
    engine_tools: Literal["native", "mcp", "none"] = "none"
    #: MCP support: full (client + server), stdio only, or none.
    mcp: Literal["full", "stdio", "none"] = "none"
    #: The client can restrict the work toolset for a control turn.
    tool_narrowing: bool = False
    condenser: bool = False
    skills_discover: bool = False
    #: Bounded missing-outcome recall.
    dispatch_recall: bool = False
    publication_guard: bool = False
    #: How the closing result is elicited: a native tool or parsed text.
    closing_channel: Literal["tool", "text"] = "tool"


class HarnessEvent(BaseModel):
    """One normalised event from a harness turn."""

    kind: Literal["action", "observation", "commentary", "result", "error"]
    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class HarnessSession(Protocol):
    """One live agent session the host drives turn by turn."""

    def send_message(self, text: str) -> None:
        """Queue ``text`` as the next user turn."""
        ...

    def run(self, timeout_s: float | None = None) -> None:
        """Run one bounded turn."""
        ...

    def close(self) -> None:
        """Release the session's runtime."""
        ...

    def on_event(self, callback: Callable[[HarnessEvent], None]) -> None:
        """Register a callback fed with every normalised event."""
        ...


class BuiltAgent(BaseModel):
    """A built agent: its live session plus the harness-rewritten prompt."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: Any
    prompt: str


class ClosingResult(BaseModel):
    """The one closing result every channel returns."""

    report: str = ""
    outcome: Optional[TurnOutcomePayload] = None


@runtime_checkable
class ClosingChannel(Protocol):
    """Elicit the closing ``(report, turn_outcome)`` from a session."""

    def elicit(
        self, session: Any, contract: Optional[OutputContract]
    ) -> ClosingResult:
        """Run the closing turn and return what it produced."""
        ...


@runtime_checkable
class HarnessAdapter(Protocol):
    """One agent runtime behind the port."""

    name: str

    def capabilities(self) -> HarnessCapabilities:
        """The declared capability set of this runtime."""
        ...

    def build_agent(
        self,
        *,
        role_name: str,
        agent_cfg: Any,
        prompt: str,
        agent_context: Any,
    ) -> BuiltAgent:
        """Build the runtime's agent and its live session."""
        ...

    def make_closing_channel(self) -> ClosingChannel:
        """The channel that elicits this runtime's closing result."""
        ...


_REGISTRY: dict[str, Callable[[], HarnessAdapter]] = {}


def register_harness(backend: str, factory: Callable[[], HarnessAdapter]) -> None:
    """Register ``factory`` under ``backend`` (a third harness is one module)."""
    _REGISTRY[backend] = factory


def registered_harnesses() -> tuple[str, ...]:
    """The registered backend names, defaults included."""
    _ensure_defaults()
    return tuple(sorted(_REGISTRY))


def _ensure_defaults() -> None:
    """Register the shipped adapters lazily (avoids an import cycle)."""
    if "openhands" not in _REGISTRY:
        from .openhands import OpenHandsHarness  # noqa: PLC0415

        register_harness("openhands", OpenHandsHarness)
    if "claude-code" not in _REGISTRY:
        from .claude_code import ClaudeCodeHarness  # noqa: PLC0415

        register_harness("claude-code", ClaudeCodeHarness)


def resolve_harness(backend: str | None) -> HarnessAdapter:
    """The adapter for ``backend``; ``None`` means OpenHands.

    Raises:
        EngineAgentUnavailable: ``backend`` is not registered. The message
            names the registered set so a config typo fails loud, never inert.
    """
    _ensure_defaults()
    name = backend or "openhands"
    factory = _REGISTRY.get(name)
    if factory is None:
        raise EngineAgentUnavailable(
            f"unknown agent backend {name!r}: registered backends are "
            f"{', '.join(sorted(_REGISTRY))}"
        )
    return factory()


def drive_turn(session: Any, message: str, timeout_s: float | None = None) -> None:
    """Send ``message`` and run one bounded turn on ``session``.

    The turn runs on a worker thread so a hung runtime cannot consume the whole
    job budget: on timeout the session is closed and the caller keeps its
    deterministic fallback chain.
    """
    session.send_message(message)
    worker = threading.Thread(target=session.run, daemon=True)
    worker.start()
    worker.join(CLOSING_TURN_TIMEOUT_S if timeout_s is None else timeout_s)
    if worker.is_alive():
        log.warning("[phase:agent] harness turn exceeded its budget - closing")
        with contextlib.suppress(Exception):
            session.close()
        worker.join(30.0)
        if worker.is_alive():
            log.error(
                "[phase:agent] harness turn worker still alive after close + 30s "
                "join - harvesting a partially completed turn"
            )
