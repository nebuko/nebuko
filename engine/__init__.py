"""engine — typed orchestration driver (Stage A of #585).

Library-only package: the driver composes the pure cores of the existing
`.github/scripts` pipeline in-process, with platform I/O behind the
`adapters.base.PlatformAdapter` protocol.
"""

from pathlib import Path

# Repo root resolved from this file (engine/ lives at the repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent

# The directory holding the existing pipeline scripts, so engine modules can
# import them deterministically (tests and driver alike).
SCRIPTS_DIR = REPO_ROOT / ".github" / "scripts"


def scripts_dir() -> Path:
    """Return the absolute path of the `.github/scripts` directory."""
    return SCRIPTS_DIR