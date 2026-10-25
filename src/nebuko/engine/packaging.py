"""Packaging helpers for the frozen release binary.

PyInstaller's ``collect_data_files`` excludes ``.py`` files by default, so the
onefile release binary shipped without the ``engine/lib`` orchestration runtime
(``ModuleNotFoundError: No module named 'config'`` at ``nebuko init``).
:func:`lib_datas` declares that tree explicitly so :data:`nebuko.engine.paths.LIB_DIR`
keeps resolving inside the frozen archive exactly as it does in the checkout.
"""

from __future__ import annotations

from nebuko.engine.paths import LIB_DIR

__all__ = ["lib_datas"]

# Destination root inside the frozen archive, matching LIB_DIR's location
# relative to the engine package (finalize.py's sibling).
_LIB_DEST_ROOT = "nebuko/engine/lib"


def lib_datas() -> list[tuple[str, str]]:
    """Return PyInstaller ``datas`` entries for the whole ``engine/lib`` tree.

    Each entry is a ``(source_path, destination_dir)`` pair preserving the
    on-disk layout under ``nebuko/engine/lib``, so the lazily imported runtime
    modules (``config``, ``workflow_state``, ...) stay importable next to
    :mod:`nebuko.engine.finalize` in the frozen binary.
    """
    datas: list[tuple[str, str]] = []
    for path in sorted(LIB_DIR.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        parent = path.relative_to(LIB_DIR).parent
        datas.append((str(path), f"{_LIB_DEST_ROOT}/{parent}"))
    return datas
