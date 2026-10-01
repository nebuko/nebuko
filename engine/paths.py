"""Legacy import bootstrap for the engine (issue #590, Stage A).

``.github/scripts/`` stays where it is and is made importable once through
:func:`ensure_import_path` (``sys.path`` append of :data:`SCRIPTS_DIR`) unless
the repository packaging already covers it — there is no ``pyproject.toml`` /
uv package root today, so the bootstrap is required.

Stub step: skeleton only. Bodies are materialised by @@dev in the implement
step.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SCRIPTS_DIR", "repo_root", "ensure_import_path"]


# @stub-protected
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"


# @stub-protected
def repo_root() -> Path:
    """Absolute path of the repository root that contains ``engine/``."""
    raise NotImplementedError


# @stub-protected
def ensure_import_path() -> None:
    """Append :data:`SCRIPTS_DIR` to ``sys.path`` (no-op when already present)."""
    raise NotImplementedError
