"""Shared import bootstrap for the engine test suites (issue #590, #741).

The Python code lives under ``src/`` (``src/nebuko``) and the frozen
orchestration runtime under ``src/nebuko/engine/lib/`` (see
``nebuko.engine.paths``). Both roots must be importable for the engine tests to
compose the port with the runtime seams.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
LIB_DIR = SRC_ROOT / "nebuko" / "engine" / "lib"

for _root in (SRC_ROOT, LIB_DIR):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
