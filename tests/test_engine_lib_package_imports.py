"""The engine library imports as ``nebuko.engine.lib.*`` with no path setup.

Issue #1165: ``src/nebuko/engine/lib/`` is a real subpackage of the packaged
``nebuko`` tree. Importing it therefore needs no ``sys.path`` entry — not in the
host process, not in an agent child process. These tests lock both halves of
that contract; the import model itself is documented in
:mod:`nebuko.engine.paths` and :mod:`nebuko.engine.lib`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from nebuko.engine.agent_runner import _child_env

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "src" / "nebuko" / "engine" / "lib"

#: The subpackages every consumer imports by package name.
LIB_MODULES = (
    "nebuko.engine.lib.workflow_state",
    "nebuko.engine.lib.config",
    "nebuko.engine.lib.agent_script",
    "nebuko.engine.lib.resolve_config",
)

#: Prints, from a clean interpreter, which ``sys.path`` entries the imports add.
_PROBE = """
import importlib, json, sys

before = list(sys.path)
for name in {modules!r}:
    importlib.import_module(name)
print(json.dumps([p for p in sys.path if p not in before]))
"""


# @stub-protected
def test_lib_modules_import_as_nebuko_subpackages() -> None:
    """Given a clean interpreter, importing the lib adds nothing to ``sys.path``."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE.format(modules=LIB_MODULES)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    added = [line for line in completed.stdout.splitlines() if line.startswith("[")]
    assert added, completed.stdout[-2000:]
    assert added[-1] == "[]", f"importing the lib extended sys.path: {added[-1]}"


# @stub-protected
def test_child_env_has_no_pythonpath_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a child script, its env carries no lib/scripts ``PYTHONPATH`` entry."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    script = REPO_ROOT / "src" / "nebuko" / "engine" / "lib" / "agent_script" / "agent_script.py"

    env = _child_env(script, None, repo_root=REPO_ROOT)

    entries = [entry for entry in env.get("PYTHONPATH", "").split(os.pathsep) if entry]
    assert str(LIB_DIR) not in entries
    assert str(script.parent.parent) not in entries
    assert env["GFA_REPO_ROOT"] == str(REPO_ROOT)
