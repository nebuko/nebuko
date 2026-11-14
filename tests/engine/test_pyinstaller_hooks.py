"""The release binary must bundle litellm's data files (issue #1113).

litellm reads its model-cost JSON at import time, so PyInstaller only includes
it when a hook provides the files. nebuko ships that hook through the
``pyinstaller40`` entry-point group — the mechanism PyInstaller itself uses to
discover hook directories — so the release workflow needs no PyInstaller flags
and the fix survives workflow-file credential restrictions.
"""

import importlib.metadata
from pathlib import Path

from nebuko.engine import _pyinstaller_hooks


def test_hook_dirs_returns_the_hook_package_directory():
    expected = Path(_pyinstaller_hooks.__file__).resolve().parent
    assert _pyinstaller_hooks.hook_dirs() == [str(expected)]


def test_the_hook_dir_is_registered_as_a_pyinstaller40_entry_point():
    eps = importlib.metadata.entry_points(group="pyinstaller40", name="hook-dirs")
    assert "nebuko.engine._pyinstaller_hooks" in {ep.module for ep in eps}


def test_the_registered_hook_dir_ships_the_litellm_hook():
    eps = importlib.metadata.entry_points(group="pyinstaller40", name="hook-dirs")
    dirs = [
        d
        for ep in eps
        if ep.module == "nebuko.engine._pyinstaller_hooks"
        for d in ep.load()()
    ]
    assert dirs, "hook-dirs entry point returned no directories"
    for hook_dir in dirs:
        assert (Path(hook_dir) / "hook-litellm.py").is_file()


def test_the_engine_hook_executes_and_declares_hidden_imports():
    import runpy

    hook_path = Path(_pyinstaller_hooks.__file__).resolve().parent / "hook-nebuko.engine.py"
    ns = runpy.run_path(str(hook_path))
    assert "nebuko.engine.lib.config" in ns["hiddenimports"]
    assert "nebuko.engine.lib.workflow_state" in ns["hiddenimports"]
