"""Configuration-path resolution for Nebuko (issues #590, #741).

Every declarative artifact Nebuko consumes — workflows, agent configs, prompts
and skills — is resolved through one layered lookup:

* ``<repo_root>/.nebuko/<kind>/`` — the repo's own overrides (optional);
* ``<checkout>/defaults/<kind>/`` — the tree shipped with Nebuko.

:class:`NebukoPaths` is the single place that knows those locations and their
priority, so the rest of the engine never builds a config path by hand.

:data:`LIB_DIR` is deliberately *not* part of that layering: it is the frozen
orchestration runtime shipped inside the package, made importable once through
:func:`ensure_import_path`.
"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from nebuko.engine.models import NebukoManifest

__all__ = [
    "ConfigKind",
    "LIB_DIR",
    "NebukoPaths",
    "defaults_dir",
    "ensure_import_path",
    "repo_root",
]

# Dot-prefixed tool-config directory in the target repo, mirroring ``.github/``.
USER_DIR_NAME = ".nebuko"
MANIFEST_NAME = "nebuko.yaml"


class ConfigKind(str, enum.Enum):
    """The four declarative artifact kinds, named after their directory."""

    WORKFLOW = "workflows"
    AGENT_CONFIG = "agents"
    PROMPT = "prompts"
    SKILL = "skills"


# @stub-protected
LIB_DIR: Final[Path] = Path(__file__).resolve().parent / "lib"


# @stub-protected
def repo_root() -> Path:
    """Absolute path of the checkout that holds the ``src/nebuko`` package."""
    return Path(__file__).resolve().parents[3]


# @stub-protected
def defaults_dir() -> Path:
    """The shipped ``defaults/`` tree, sibling of ``src/`` in the checkout."""
    return repo_root() / "defaults"


# @stub-protected
def ensure_import_path() -> None:
    """Append :data:`LIB_DIR` to ``sys.path`` (no-op when already present)."""
    path = str(LIB_DIR)
    if path not in sys.path:
        sys.path.append(path)


# @stub-protected
@dataclass(frozen=True)
class NebukoPaths:
    """Immutable, layered view of where Nebuko's config artifacts live.

    Attributes:
        repo_root: The checkout being operated on; its ``.nebuko/`` holds the
            user overrides.
        user_dir: ``<repo_root>/.nebuko`` — may not exist.
        defaults_dir: The shipped ``defaults/`` tree, located from the package.
        manifest: The parsed ``.nebuko/nebuko.yaml`` (an empty manifest when the
            file is absent).
    """

    repo_root: Path
    user_dir: Path
    defaults_dir: Path
    manifest: NebukoManifest

    # @stub-protected
    @classmethod
    def discover(cls, repo_root: Path) -> "NebukoPaths":
        """Resolve the layers for ``repo_root``, reading its manifest if present."""
        root = Path(repo_root).resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"repo_root is not a directory: {root}")
        user_dir = root / USER_DIR_NAME
        return cls(
            repo_root=root,
            user_dir=user_dir,
            defaults_dir=defaults_dir(),
            manifest=_load_manifest(user_dir / MANIFEST_NAME),
        )

    # @stub-protected
    def search_roots(self, kind: ConfigKind) -> list[Path]:
        """The directories scanned for ``kind``, highest priority first."""
        roots: list[Path] = []
        if kind is ConfigKind.SKILL and self.manifest.skills_dir:
            roots.append(self._relative_to_repo(self.manifest.skills_dir))
        roots.append(self.user_dir / kind.value)
        roots.append(self.defaults_dir / kind.value)
        return _dedupe(roots)

    # @stub-protected
    def resolve(self, kind: ConfigKind, name: str) -> Path:
        """Return the highest-priority existing ``kind/name``, else raise."""
        roots = self.search_roots(kind)
        for root in roots:
            candidate = root / name
            if candidate.is_file():
                return candidate
        searched = ", ".join(str(root) for root in roots)
        raise FileNotFoundError(f"{kind.value}/{name} not found in: {searched}")

    # @stub-protected
    def list(self, kind: ConfigKind) -> dict[str, Path]:
        """Merged ``name -> path`` view, user entries overriding defaults."""
        merged: dict[str, Path] = {}
        for root in reversed(self.search_roots(kind)):  # defaults first
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                if entry.is_file():
                    merged[entry.name] = entry
        return merged

    def _relative_to_repo(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.repo_root / path


def _load_manifest(path: Path) -> NebukoManifest:
    """Parse the optional manifest; an absent file yields an empty manifest."""
    if not path.is_file():
        return NebukoManifest()
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return NebukoManifest.model_validate(raw)


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out
