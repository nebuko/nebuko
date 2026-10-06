"""Shared import bootstrap for the engine test suites (issue #590).

``engine/`` and ``adapters/`` live at the repository root while the migrated
orchestration library lives under ``engine/lib/`` (see
``engine.paths.ensure_import_path``). Both roots must be importable for the
engine tests to compose the port with the runtime seams.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "engine" / "lib"

for _root in (REPO_ROOT, LIB_DIR):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
