"""Bridge-import regression for the soft-kill crash (issue #766).

The engine's ``publish`` phase used to re-import the legacy bridge
(``prepare_comment``) on the hot path via a bare ``from prepare_comment import
...``, after the in-process agent run had already executed in the same
interpreter. The live run raised ``ModuleNotFoundError`` there.

``engine.run_loop._load_bridge_module`` loads the legacy modules by absolute
path (``importlib.util.spec_from_file_location`` against
``engine.paths.SCRIPTS_DIR``), so the resolution is independent of ``sys.path``
and ``sys.modules`` — and ``Engine.preflight()`` resolves the bridge before the
agent runs, so a broken checkout fails fast instead of mid-run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from adapters.local import LocalAdapter
from engine.models import Trigger
from engine.paths import SCRIPTS_DIR, repo_root
from engine import run_loop
from engine.run_loop import Engine


@pytest.fixture
def fresh_bridge_cache(monkeypatch):
    """Isolate the module-level bridge cache so a test can force a fresh load."""
    monkeypatch.setattr(run_loop, "_BRIDGE_CACHE", {})


@pytest.fixture
def poisoned_import_env(monkeypatch):
    """Reproduce the state the in-process agent run leaves behind (issue #766).

    ``SCRIPTS_DIR`` is gone from ``sys.path`` and a stale ``None`` entry for the
    module we expect the bridge to load sits in ``sys.modules`` — a bare
    ``import prepare_comment`` would raise ``ModuleNotFoundError``.
    """
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path if p != str(SCRIPTS_DIR)]
    )
    monkeypatch.setitem(sys.modules, "prepare_comment", None)


def _engine(tmp_path) -> Engine:
    trigger = Trigger(author="olivier-norture", body="@@dev continue", type="issue_comment", number=766)
    adapter = LocalAdapter(tmp_path / "runs" / "run-1", trigger=trigger)
    return Engine(adapter=adapter, repo_root=repo_root(), env={"GITHUB_REPOSITORY": "nebuko/nebuko"})


class TestBridgeResolution:
    def test_loads_by_absolute_path_with_a_poisoned_import_env(
        self, fresh_bridge_cache, poisoned_import_env
    ):
        module = run_loop._load_bridge_module("prepare_comment")

        assert Path(module.__file__).resolve() == (SCRIPTS_DIR / "prepare_comment.py").resolve()
        assert callable(module.build_comment_body)

    def test_the_engine_bridge_survives_a_poisoned_import_env(
        self, tmp_path, fresh_bridge_cache, poisoned_import_env
    ):
        bridge = _engine(tmp_path)._resolve_bridge()

        assert callable(bridge.validate_agent_output)
        assert callable(bridge.build_comment_body)

    def test_dispatch_recovery_survives_a_poisoned_import_env(
        self, fresh_bridge_cache, monkeypatch
    ):
        monkeypatch.setattr(
            sys, "path", [p for p in sys.path if p != str(SCRIPTS_DIR)]
        )
        monkeypatch.setitem(sys.modules, "validate_output", None)
        text = '<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "soft_kill"}} -->'

        assert run_loop._dispatch_from_text(text) == {"status": "soft_kill"}


class TestPreflight:
    def test_resolves_the_bridge_once(self, tmp_path, monkeypatch, fresh_bridge_cache):
        calls: list[str] = []
        real_load = run_loop._load_bridge_module

        def reporting_load(name):
            calls.append(name)
            return real_load(name)

        monkeypatch.setattr(run_loop, "_load_bridge_module", reporting_load)
        engine = _engine(tmp_path)

        first = engine.preflight()
        second = engine.preflight()

        assert first is None
        assert calls == ["validate_output", "prepare_comment"]
        assert engine._resolve_bridge() is engine._resolve_bridge()

    def test_fails_fast_when_the_bridge_module_is_missing(
        self, tmp_path, monkeypatch, fresh_bridge_cache
    ):
        monkeypatch.setattr(run_loop, "SCRIPTS_DIR", tmp_path / "absent")
        engine = _engine(tmp_path)

        with pytest.raises(ModuleNotFoundError, match="legacy bridge module not found"):
            engine.preflight()

    def test_a_poisoned_cache_is_not_trusted_for_a_missing_module(
        self, tmp_path, monkeypatch, fresh_bridge_cache
    ):
        """The fail-fast check must not be fooled by a stale ``sys.modules`` entry."""
        monkeypatch.setattr(run_loop, "SCRIPTS_DIR", tmp_path / "absent")
        monkeypatch.setitem(sys.modules, "validate_output", None)
        engine = _engine(tmp_path)

        with pytest.raises(ModuleNotFoundError):
            engine.preflight()
