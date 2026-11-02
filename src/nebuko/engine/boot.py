"""Engine boot helpers: root-logging parity and agent-SDK pre-warming (issue #1266).

The engine's import chain used to drag ``openhands.sdk`` in at module top (via
``engine.lib.git``), costing ~5s warm / ~10s cold before the first log line. That
import is now off the startup path, which changes two things this module restores
or exploits:

* **Logging parity.** Importing ``openhands.sdk`` runs its ``setup_logging()``,
  which installs the root console handler (JSON under CI / ``LOG_JSON``, Rich
  otherwise) *before* ``main()`` got to ``logging.basicConfig``. Now that the SDK
  loads later, :func:`configure_logging` installs the same handler up front; when
  the SDK finally imports, it sees a stream handler and adds none of its own, so
  the console output is unchanged.
* **Pre-warming.** The agent still needs the SDK. :func:`prewarm_agent_sdk`
  imports it on a background thread while the main thread spends its time
  waiting on ``gh``/``git`` subprocesses (normalize -> agent phases), so the import
  is hidden behind I/O instead of serialized before it. The SDK is imported at the
  same point in the run as before (process start, ahead of any env overlay), so it
  sees the same environment it always did.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import threading
from typing import Mapping

__all__ = ["configure_logging", "prewarm_agent_sdk"]

log = logging.getLogger(__name__)

_TRUE = frozenset({"1", "true", "yes"})
_FALSE = frozenset({"0", "false", "no"})
_SDK_MODULE = "openhands.sdk"
#: Third-party loggers ``openhands.sdk.logger`` quiets at import; quieted up front
#: too, or a DEBUG root would let litellm's own import-time debug chatter through.
_QUIETED = {"litellm": logging.ERROR, "LiteLLM": logging.ERROR, "openai": logging.ERROR,
            "httpcore": logging.WARNING, "httpx": logging.WARNING, "libtmux": logging.WARNING}
#: Same record layout ``openhands.sdk.logger`` uses for its JSON console handler.
_JSON_FMT = "%(asctime)s %(levelname)s %(name)s %(filename)s %(lineno)d %(message)s"


def _flag(env: Mapping[str, str], key: str, default: str) -> bool:
    return env.get(key, default).lower() in _TRUE


def _sdk_installed() -> bool:
    # find_spec on a top-level name only locates the package; it imports nothing.
    return importlib.util.find_spec("openhands") is not None


def _level(env: Mapping[str, str]) -> int:
    if _flag(env, "DEBUG", "false"):
        return logging.DEBUG
    return logging.getLevelNamesMapping().get(
        env.get("LOG_LEVEL", "INFO").upper(), logging.INFO
    )


def _console_handler(env: Mapping[str, str]) -> logging.Handler:
    """The console handler ``openhands.sdk.logger.setup_logging`` would install."""
    in_ci = _flag(env, "CI", "false") or bool(env.get("GITHUB_ACTIONS"))
    if _flag(env, "LOG_JSON", "false") or in_ci:
        from pythonjsonlogger.json import JsonFormatter

        handler: logging.Handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter(fmt=_JSON_FMT))
        return handler
    from rich.console import Console
    from rich.logging import RichHandler

    handler = RichHandler(
        console=Console(stderr=True),
        omit_repeated_times=False,
        rich_tracebacks=_flag(env, "LOG_RICH_TRACEBACKS", "true"),
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def configure_logging(env: Mapping[str, str]) -> None:
    """Install the root console handler the engine has always logged through.

    Mirrors what an early ``openhands.sdk`` import used to do; falls back to the
    previous ``logging.basicConfig(level=INFO)`` when the SDK is not installed,
    its auto-config is disabled, or its formatter dependencies are missing. A
    root logger that already has a stream handler (embedding, pytest) is left
    alone, exactly as both ``basicConfig`` and the SDK do.
    """
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        return
    if not _sdk_installed() or not _flag(env, "LOG_AUTO_CONFIG", "true"):
        logging.basicConfig(level=logging.INFO)
        return
    level = _level(env)
    try:
        handler = _console_handler(env)
    except ImportError:
        logging.basicConfig(level=logging.INFO)
        return
    handler.setLevel(level)
    root.setLevel(level)
    root.addHandler(handler)
    if not _flag(env, "DEBUG_LLM", "false"):  # the SDK owns the DEBUG_LLM opt-in
        for name, quiet in _QUIETED.items():
            logger = logging.getLogger(name)
            logger.setLevel(quiet)
            logger.propagate = False


def _import_sdk() -> None:
    try:
        importlib.import_module(_SDK_MODULE)
    except Exception:  # noqa: BLE001 - the agent path re-imports and surfaces it
        log.debug("background %s import failed", _SDK_MODULE, exc_info=True)


def prewarm_agent_sdk(env: Mapping[str, str]) -> threading.Thread | None:
    """Start importing the agent SDK on a daemon thread; return it (or ``None``).

    Opt out with ``NEBUKO_PREWARM_SDK=0``. Skipped when the SDK is not installed.
    Daemon, so a run that exits before reaching the agent never waits on it; a
    later main-thread import of the same module simply blocks on the import lock
    until the background import completes.
    """
    if env.get("NEBUKO_PREWARM_SDK", "1").lower() in _FALSE:
        return None
    if not _sdk_installed():
        return None
    thread = threading.Thread(target=_import_sdk, name="nebuko-sdk-prewarm", daemon=True)
    thread.start()
    return thread
