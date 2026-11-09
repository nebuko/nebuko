"""Engine startup cost guards (issue #1266).

The engine used to spend ~5s warm / ~10s cold importing ``openhands.sdk`` before
its first log line, because ``engine.lib.git`` imported the SDK's logger at module
top. These tests pin: the startup import chain stays SDK-free; moving the import
later does not change the console log format; and the background pre-warm is safe.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from nebuko.engine import boot

REPO_ROOT = Path(__file__).resolve().parents[2]
HEAVY = ("openhands", "litellm")
#: Env keys that steer the SDK's logging setup; scrubbed so each case is explicit.
_LOGGING_ENV = {
    "CI", "GITHUB_ACTIONS", "DEBUG", "LOG_JSON", "LOG_LEVEL", "LOG_AUTO_CONFIG",
    "LOG_TO_FILE", "LOG_RICH_TRACEBACKS", "NEBUKO_PREWARM_SDK",
}


def _sdk_available() -> bool:
    return boot._sdk_installed()


def _run(code: str, **env: str) -> str:
    base = {k: v for k, v in os.environ.items() if k not in _LOGGING_ENV}
    base["OPENHANDS_SUPPRESS_BANNER"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env={**base, **env},
        cwd=REPO_ROOT,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class TestStartupImportChain:
    def test_importing_run_loop_does_not_load_the_agent_sdk(self):
        loaded = _run(
            """
            import sys
            import nebuko.engine.run_loop
            print(sorted({m.split('.')[0] for m in sys.modules} & set(%r)))
            """
            % (HEAVY,)
        )
        assert loaded == "[]"

    def test_git_modules_log_through_the_stdlib_logger(self):
        from nebuko.engine.lib.git import branch_scaffold, git_session, pr_finalize

        for module in (branch_scaffold, git_session, pr_finalize):
            assert module.logger is logging.getLogger(module.__name__)
            assert module.logger.propagate is True


def _console_handlers_after_sdk_import(*, configure_first: bool, **env: str) -> str:
    """Describe root's console handlers once the SDK has imported."""
    return _run(
        f"""
        import logging
        if {configure_first!r}:
            from nebuko.engine.boot import configure_logging
            import os
            configure_logging(os.environ)
        import openhands.sdk
        root = logging.getLogger()
        print(root.level, [
            (type(h).__name__, type(h.formatter).__name__, getattr(h.formatter, '_fmt', None), h.level)
            for h in root.handlers if isinstance(h, logging.StreamHandler)
        ])
        """,
        **env,
    )


@pytest.mark.skipif(not _sdk_available(), reason="needs the runtime extra")
@pytest.mark.slow
class TestLoggingParity:
    """configure_logging + late SDK import == the old early SDK import."""

    @pytest.mark.parametrize(
        "env",
        [
            {"GITHUB_ACTIONS": "true"},
            {"LOG_JSON": "1", "LOG_LEVEL": "WARNING"},
            {"DEBUG": "1", "CI": "true"},
            {},
        ],
        ids=["github-actions-json", "log-json-warning", "debug-ci", "local-rich"],
    )
    def test_console_handler_matches_the_early_sdk_import(self, env):
        before = _console_handlers_after_sdk_import(configure_first=False, **env)
        after = _console_handlers_after_sdk_import(configure_first=True, **env)
        assert after == before

    def test_auto_config_off_keeps_the_previous_basic_config(self):
        out = _run(
            """
            import logging, os
            from nebuko.engine.boot import configure_logging
            configure_logging(os.environ)
            root = logging.getLogger()
            print(root.level, [type(h.formatter).__name__ for h in root.handlers])
            """,
            LOG_AUTO_CONFIG="false",
        )
        assert out == f"{logging.INFO} ['Formatter']"


class TestConfigureLogging:
    def test_an_existing_stream_handler_is_left_alone(self):
        out = _run(
            """
            import logging, os
            from nebuko.engine.boot import configure_logging
            existing = logging.StreamHandler()
            logging.getLogger().addHandler(existing)
            configure_logging(os.environ)
            print(logging.getLogger().handlers == [existing])
            """,
            GITHUB_ACTIONS="true",
        )
        assert out == "True"

    def test_without_the_sdk_it_falls_back_to_basic_config(self):
        out = _run(
            """
            import logging, os
            from nebuko.engine import boot
            boot._sdk_installed = lambda: False
            boot.configure_logging(os.environ)
            root = logging.getLogger()
            print(root.level, [type(h.formatter).__name__ for h in root.handlers])
            """,
            GITHUB_ACTIONS="true",
        )
        assert out == f"{logging.INFO} ['Formatter']"


class TestPrewarm:
    def test_opt_out_starts_no_thread(self, monkeypatch):
        monkeypatch.setattr(boot, "_sdk_installed", lambda: True)
        assert boot.prewarm_agent_sdk({"NEBUKO_PREWARM_SDK": "0"}) is None

    def test_missing_sdk_starts_no_thread(self, monkeypatch):
        monkeypatch.setattr(boot, "_sdk_installed", lambda: False)
        assert boot.prewarm_agent_sdk({}) is None

    def test_imports_the_sdk_on_a_daemon_thread(self, monkeypatch):
        seen: list[tuple[str, bool]] = []

        def fake_import(name):
            seen.append((name, threading.current_thread() is threading.main_thread()))

        monkeypatch.setattr(boot, "_sdk_installed", lambda: True)
        monkeypatch.setattr(boot.importlib, "import_module", fake_import)
        thread = boot.prewarm_agent_sdk({})
        assert thread is not None and thread.daemon
        thread.join(5)
        assert seen == [("openhands.sdk", False)]

    def test_a_failing_background_import_is_swallowed(self, monkeypatch):
        def boom(name):
            raise ImportError(name)

        monkeypatch.setattr(boot, "_sdk_installed", lambda: True)
        monkeypatch.setattr(boot.importlib, "import_module", boom)
        errors: list[BaseException] = []
        monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_value))
        thread = boot.prewarm_agent_sdk({})
        thread.join(5)
        assert errors == []
