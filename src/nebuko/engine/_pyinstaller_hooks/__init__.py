"""PyInstaller hooks shipped by nebuko (issue #1113).

The release binary is built with ``pyinstaller --onefile`` from
``packaging/entry.py``. litellm reads its model-cost JSON at import time, so
those data files must be bundled or every command that imports the SDK crashes
with ``FileNotFoundError``. The hooks are registered through the
``pyinstaller40`` entry-point group in ``pyproject.toml`` — the same mechanism
PyInstaller itself uses — so the release workflow needs no PyInstaller flags.
"""


def hook_dirs() -> list[str]:
    """Return the hook directory for the ``pyinstaller40`` entry point."""
    from pathlib import Path

    return [str(Path(__file__).parent)]
