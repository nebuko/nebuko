"""Guard: the engine imports its library by package name, never via ``sys.path``.

Issue #1165 removed the runtime import bootstrap: ``src/nebuko/engine/lib/`` is
an ordinary subpackage of ``nebuko``, so every consumer imports
``nebuko.engine.lib.<subpackage>`` and no module has to extend ``sys.path`` to
make that work. These tests keep the hacks from creeping back in.

See the module docstrings of :mod:`nebuko.engine.paths` and
:mod:`nebuko.engine.lib` for the import model this locks in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Files still allowed to touch ``sys.path``. Target: empty after the migration
#: — pytest's ``pythonpath = ["src"]`` makes the package importable on its own.
# @stub-protected
ALLOWED: Final[frozenset[str]] = frozenset()

#: The checker itself names the forbidden calls, so it cannot be scanned.
_SELF = Path(__file__).resolve()

# Built from parts so this module never matches its own scan.
_PATH_MUTATORS: Final[tuple[str, ...]] = ("sys.path" + ".insert(", "sys.path" + ".append(")
_BOOTSTRAP_FN: Final[str] = "ensure" + "_import_path"


def _python_files(*roots: Path) -> list[Path]:
    """Every ``*.py`` file under ``roots``, excluding this checker."""
    found: list[Path] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.resolve() != _SELF:
                found.append(path)
    return found


def _offenders(needles: tuple[str, ...]) -> list[str]:
    """``path:line`` for every occurrence of ``needles`` outside ``ALLOWED``."""
    hits: list[str] = []
    for path in _python_files(REPO_ROOT / "src", REPO_ROOT / "tests"):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in ALLOWED:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(needle in line for needle in needles):
                hits.append(f"{relative}:{number}: {line.strip()}")
    return hits


# @stub-protected
def test_no_sys_path_manipulation() -> None:
    """Given the packaged layout, no module mutates ``sys.path`` to import lib."""
    offenders = _offenders(_PATH_MUTATORS)

    assert offenders == [], "sys.path manipulation outside the allowlist:\n" + "\n".join(offenders)


# @stub-protected
def test_no_ensure_import_path_callers() -> None:
    """Given package-qualified imports, the bootstrap helper has no callers left."""
    offenders = _offenders((_BOOTSTRAP_FN,))

    assert offenders == [], "import-bootstrap references left behind:\n" + "\n".join(offenders)
