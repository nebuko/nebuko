"""Import bootstrap for the engine runtime library (issue #590, Stage A; #769).

The orchestration Python the engine imports at runtime lives under
``engine/lib/`` and is made importable once through
:func:`ensure_import_path` (``sys.path`` append of :data:`LIB_DIR`) unless the
repository packaging already covers it — there is no ``pyproject.toml`` / uv
package root today, so the bootstrap is required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

__all__ = ["LIB_DIR", "repo_root", "ensure_import_path"]


# @stub-protected
LIB_DIR: Final[Path] = Path(__file__).resolve().parent / "lib"


# @stub-protected
def repo_root() -> Path:
    """Absolute path of the repository root that contains ``engine/``."""
    return Path(__file__).resolve().parents[1]


# @stub-protected
def ensure_import_path() -> None:
    """Append :data:`LIB_DIR` to ``sys.path`` (no-op when already present)."""
    path = str(LIB_DIR)
    if path not in sys.path:
        sys.path.append(path)
