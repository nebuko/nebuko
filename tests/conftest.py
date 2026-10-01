"""Shared import bootstrap for the Stage A engine test suites (issue #590).

``engine/`` and ``adapters/`` live at the repository root while the legacy
orchestration scripts stay under ``.github/scripts/`` (see
``engine.paths.ensure_import_path``). Both roots must be importable for the
engine tests to compose the port with the legacy seams.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / ".github" / "scripts"

for _root in (REPO_ROOT, SCRIPTS_DIR):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
