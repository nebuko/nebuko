"""Configuration-path resolution for Nebuko (issues #590, #741, #1165).

Every declarative artifact Nebuko consumes — workflows, agent configs, prompts
and skills — is resolved through one layered lookup:

* ``<repo_root>/.nebuko/<kind>/`` — the repo's own overrides (optional);
* ``<checkout>/defaults/<kind>/`` — the tree shipped with Nebuko.

:class:`NebukoPaths` is the single place that knows those locations and their
priority, so the rest of the engine never builds a config path by hand.

How to import the orchestration library
---------------------------------------
``src/nebuko/engine/lib/`` is an ordinary subpackage of ``nebuko``. Import it
by its package name, exactly like any other engine module::

    from nebuko.engine.lib.workflow_state import WorkflowState
    from nebuko.engine.lib.config import AppConfig

Never put a directory on ``sys.path`` to make these imports work, and never
add ``# noqa: E402`` to them: the import is a normal package import, so it
belongs with the other imports at the top of the module.

How it works
------------
The layout already lives inside the packaged tree, and setuptools discovers it
through ``[tool.setuptools.packages.find] where = ["src"]`` — every directory
with an ``__init__.py`` under ``src/`` becomes a package member, so
``nebuko.engine.lib.<subpackage>`` resolves without any path configuration.
That is also what lets mypy/pyright resolve the library and what makes the
PyInstaller build see it through static analysis.

:data:`LIB_DIR` is therefore a *filesystem* constant only — a way to name the
directory that holds the shipped runtime files (fixtures, entry-point scripts)
— and is never used to extend the import path.
"""

from __future__ import annotations

import enum
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Mapping

from nebuko.engine.models import NebukoManifest

__all__ = [
    "ConfigKind",
    "ConfigPathAnchor",
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
    def directory(self, kind: ConfigKind) -> Path:
        """Highest-priority directory that exists for ``kind``.

        The engine's prompt lookup takes a single base directory, so it uses
        this instead of :meth:`search_roots` (which always returns both layers,
        existing or not, for per-file workflow resolution). When neither layer
        exists the shipped defaults directory is returned, so callers still get
        a usable path.
        """
        for root in self.search_roots(kind):
            if root.is_dir():
                return root
        return self.defaults_dir / kind.value

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


# @stub-protected
@dataclass(frozen=True)
class ConfigPathAnchor:
    """Resolve a caller-supplied ``AGENT_CONFIG_PATH`` to an absolute path.

    A relative ``agent_config_path`` is documented as relative to the *caller's*
    repository (``docs/adoption.md``), but CI imports Nebuko from a separate
    checkout (``.nebuko-engine``) while running with the caller's workspace as
    cwd. Anchoring such a value at :func:`repo_root` therefore injects the engine
    checkout into the caller's path (issue #930), so the anchor is instead built
    from the caller's workspace — ``$GITHUB_WORKSPACE`` first, then the cwd the
    engine started in, then the engine checkout as a last resort.

    Attributes:
        raw: The caller-supplied value verbatim.
        caller_root: The workspace relative values are anchored at.
        engine_root: The nebuko checkout, used only to reach packaged defaults.
    """

    raw: str
    caller_root: Path
    engine_root: Path

    # @stub-protected
    @classmethod
    def resolve_caller_root(
        cls,
        env: Mapping[str, str] | None = None,
        start_cwd: Path | None = None,
    ) -> Path:
        """The workspace to anchor relative values at, by precedence.

        ``$GITHUB_WORKSPACE`` outranks :func:`Path.cwd` because the anchor may run
        after the engine has ``chdir``-ed into the child's temp workdir
        (issue #717); it is only honored when it names an existing directory.
        """
        source = os.environ if env is None else env
        workspace = source.get("GITHUB_WORKSPACE")
        if workspace and Path(workspace).is_dir():
            return Path(workspace).resolve()
        cwd = Path.cwd() if start_cwd is None else Path(start_cwd)
        if cwd.is_dir():
            return cwd.resolve()
        return repo_root()

    # @stub-protected
    @classmethod
    def from_env(
        cls,
        raw: str,
        *,
        engine_root: Path,
        start_cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "ConfigPathAnchor":
        """Build an anchor for ``raw``, deriving the caller workspace when absent."""
        return cls(
            raw=raw,
            caller_root=cls.resolve_caller_root(env, start_cwd),
            engine_root=engine_root,
        )

    # @stub-protected
    def resolve(self) -> str:
        """The absolute path ``raw`` names, anchored at :attr:`caller_root`.

        An absolute value is honored as-is. A relative value with a separator is
        taken relative to the caller workspace (the caller named the location).
        A bare name is looked up in the caller's ``.nebuko/agents/`` first — the
        documented lookup — then the engine's packaged ``defaults/agents/``,
        finally falling back to the caller workspace.
        """
        if os.path.isabs(self.raw):
            return self.raw
        if os.sep in self.raw:
            return str(self.caller_root / self.raw)
        for root in (self.caller_root, self.engine_root):
            try:
                return str(
                    NebukoPaths.discover(root).resolve(
                        ConfigKind.AGENT_CONFIG, self.raw
                    )
                )
            except (FileNotFoundError, NotADirectoryError):
                continue
        return str(self.caller_root / self.raw)


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
