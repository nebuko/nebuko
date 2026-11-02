"""CI caches the engine venv with its bytecode (issue #1266).

A cold ``__pycache__`` roughly doubles the engine's import time (~11s vs ~5s),
so the run-task job restores ``.nebuko-engine/.venv`` from a cache keyed on the
exact interpreter and the lock, and compiles bytecode only on a cache miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW_YAML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nebuko-workflow.yaml"


@pytest.fixture
def steps() -> dict[str, dict]:
    workflow = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    return {s.get("name"): s for s in workflow["jobs"]["run-task"]["steps"]}


def test_engine_venv_is_cached_per_interpreter_and_lock(steps):
    cache = steps["Restore engine venv"]
    assert cache["uses"].startswith("actions/cache@")
    assert cache["with"]["path"] == ".nebuko-engine/.venv"
    key = cache["with"]["key"]
    assert "steps.setup-python.outputs.python-version" in key
    assert ".nebuko-engine/uv.lock" in key
    assert steps["Set up Python"]["id"] == "setup-python"


def test_bytecode_is_compiled_only_on_a_cache_miss(steps):
    sync = steps["Sync locked dependencies"]
    assert sync["env"]["VENV_CACHE_HIT"] == "${{ steps.engine-venv.outputs.cache-hit }}"
    assert "--compile-bytecode" in sync["run"]


def test_cache_restores_before_the_sync(steps):
    names = list(steps)
    assert names.index("Restore engine venv") < names.index("Sync locked dependencies")
