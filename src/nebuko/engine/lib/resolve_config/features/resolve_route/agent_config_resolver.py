"""Agent-config name selection and layered lookup (issue #1479, ADR 0017).

Two lanes pick an agent config differently:

- **project** lane: the project's ``.nebuko/agents`` first, then the shipped
  ``defaults/agents``.
- **personal** lane: the personal repo's ``.nebuko/agents`` first, then the
  shipped ``defaults/agents``. The project's own ``.nebuko/agents`` is never
  read there: a repo-supplied config could name the endpoint the user's key is
  sent to, and the dev agent can edit that directory on its own branch.

The personal repo is checked out at :data:`PERSONAL_CHECKOUT_DIR` under the
caller root by the Lane D reusable-workflow step.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, Mapping, Optional

import yaml

__all__ = [
    "AgentConfigNotFound",
    "AgentConfigResolver",
    "PERSONAL_CHECKOUT_DIR",
    "PERSONAL_REPO_SUFFIX",
    "PERSONAL_SNAPSHOT_ENV",
    "PersonalSnapshot",
    "ResolvedAgentConfig",
    "current_personal_snapshot",
    "frozen_agent_config_text",
    "personal_run_from_env",
    "personal_snapshot_child_env",
    "personal_snapshot_scope",
    "required_secret_envs",
    "required_secret_names_from_text",
    "resolve_personal_snapshot",
    "select_config_name",
]

Lane = Literal["project", "personal"]

#: Suffix of the personal repo's name. GitHub sets ``GITHUB_REPOSITORY`` to the
#: repository a run belongs to, so a run whose repository ends in this suffix
#: is the personal repo's own run. The caller cannot change that value.
PERSONAL_REPO_SUFFIX = "/nebuko-personal"

#: Where the Lane D reusable workflow checks the personal repo out, relative to
#: the caller root (the project workspace).
PERSONAL_CHECKOUT_DIR = ".nebuko-personal"


def personal_run_from_env(env: Mapping[str, str]) -> bool:
    """True when this run is the personal repo's own run (issue #1479).

    Reads ``GITHUB_REPOSITORY`` as set by GitHub, not any caller-supplied flag.
    """
    return (env.get("GITHUB_REPOSITORY") or "").strip().endswith(PERSONAL_REPO_SUFFIX)


class AgentConfigNotFound(Exception):
    """An agent-config name found in none of the searched layers."""

    def __init__(self, name: str, searched: list[str], hint: str = "") -> None:
        self.name = name
        self.searched = searched
        self.hint = hint
        super().__init__(str(self))

    def __str__(self) -> str:
        text = (
            f"agent config '{self.name}' not found; "
            f"searched layers: {', '.join(self.searched)}"
        )
        return f"{text} ({self.hint})" if self.hint else text


@dataclass(frozen=True)
class ResolvedAgentConfig:
    """A config file found by :meth:`AgentConfigResolver.resolve`.

    Attributes:
        path: The file that holds the config.
        layer: The layer that provided it: ``"project"``, ``"personal"`` or
            ``"shipped"``.
        searched: Layers consulted up to and including the hit.
    """

    path: Path
    layer: str
    searched: list[str]


@dataclass(frozen=True)
class AgentConfigResolver:
    """Ordered agent-config search for one lane.

    Attributes:
        lane: The run's lane; it fixes which override directory is consulted.
        override_dir: The lane's ``.nebuko/agents`` directory, or ``None`` when
            the lane has no checkout (personal repo absent).
        shipped_dir: The shipped ``defaults/agents`` directory.
    """

    lane: Lane
    override_dir: Optional[Path]
    shipped_dir: Path

    @classmethod
    def for_run(
        cls,
        lane: Lane,
        caller_root: Path,
        personal_root: Optional[Path],
        shipped_dir: Path,
    ) -> "AgentConfigResolver":
        """The resolver for ``lane``: project override or personal checkout."""
        if lane == "personal":
            override = personal_root / ".nebuko" / "agents" if personal_root else None
        else:
            override = caller_root / ".nebuko" / "agents"
        return cls(lane=lane, override_dir=override, shipped_dir=shipped_dir)

    def layers(self) -> list[tuple[str, Path]]:
        """Search layers in precedence order: lane override, then shipped."""
        found: list[tuple[str, Path]] = []
        if self.override_dir is not None:
            found.append((self.lane, self.override_dir))
        found.append(("shipped", self.shipped_dir))
        return found

    def resolve(self, name: str) -> ResolvedAgentConfig:
        """Find a bare config file name; raise :class:`AgentConfigNotFound` otherwise."""
        bare = name.strip()
        searched: list[str] = []
        for layer, root in self.layers():
            searched.append(layer)
            candidate = root / bare
            if bare and (candidate.is_file() or frozen_agent_config_text(candidate) is not None):
                return ResolvedAgentConfig(path=candidate, layer=layer, searched=list(searched))
        hint = (
            ""
            if self.override_dir is not None or self.lane != "personal"
            else "the personal repo is not checked out"
        )
        raise AgentConfigNotFound(name, searched, hint)


def select_config_name(
    *,
    flag: Optional[str],
    previous: Optional[str],
    personal_manifest: Optional[str],
    project_manifest: Optional[str],
    carry_previous: bool,
) -> tuple[Optional[str], Optional[str]]:
    """The config name and its source, first non-empty wins (issue #1479).

    Order: the ``--config`` flag, the previous state's name, the personal
    manifest (personal lane only; pass ``None`` elsewhere), the project
    manifest. ``None`` means the shipped default applies.

    ``carry_previous`` is False on a takeover or a lane switch: the previous
    name belongs to the other person or lane and must not be looked up again.

    Returns:
        ``(name, source)``; ``source`` is ``"flag"``, ``"previous"``,
        ``"personal_manifest"``, ``"project_manifest"`` or ``None``.
    """
    candidates = (
        ("flag", flag),
        ("previous", previous if carry_previous else None),
        ("personal_manifest", personal_manifest),
        ("project_manifest", project_manifest),
    )
    for source, value in candidates:
        if value and value.strip():
            return value.strip(), source
    return None, None


def required_secret_envs(config_path: Path) -> list[str]:
    """The ``api_token_env`` names a config file requires, sorted and unique.

    A personal-run config is read from the run's frozen snapshot, not the disk.
    """
    text = frozen_agent_config_text(config_path)
    if text is None:
        text = config_path.read_text(encoding="utf-8")
    return required_secret_names_from_text(text)


def required_secret_names_from_text(text: str) -> list[str]:
    """The ``api_token_env`` names in a config's YAML text, sorted and unique."""
    raw = yaml.safe_load(text) or {}
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "api_token_env" and isinstance(value, str) and value.strip():
                    names.add(value.strip())
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return sorted(names)


#: Carries the frozen personal snapshot into the agent child process. The
#: engine sets it from its own memory, so a dev agent editing the checkout on
#: disk cannot change what the child reads.
PERSONAL_SNAPSHOT_ENV = "NEBUKO_PERSONAL_SNAPSHOT"


@dataclass(frozen=True)
class PersonalSnapshot:
    """The personal repo's agent configs and manifest, read once before the run's first step (issue #1485).

    The dev agent shares the checkout, so every later read in the run (routing,
    the LLM resolver, the agent child) takes its config text from here. Modelled
    on the base snapshot (#1475).

    Attributes:
        root: The personal checkout, ``<workspace>/.nebuko-personal``.
        agents: Bare file name -> text of every file in ``.nebuko/agents``.
        config_name: The manifest's ``agent_config`` name, or ``None``.
        layer: ``"personal"`` when ``config_name`` is in ``agents``,
            ``"shipped"`` when it is not, ``None`` without a name.
        secret_names: ``api_token_env`` names the ``config_name`` config needs.
        manifest_ignored: Manifest keys other than ``agent_config``, sorted.
        note: Why part of the personal repo was skipped, or ``None``.
    """

    root: str
    agents: Mapping[str, str]
    config_name: Optional[str]
    layer: Optional[str]
    secret_names: tuple[str, ...]
    manifest_ignored: tuple[str, ...] = ()
    note: Optional[str] = None

    def text_for(self, path: str | Path) -> Optional[str]:
        """The frozen text of ``path`` when it is a file of the personal agents dir."""
        candidate = Path(path)
        if candidate.parent != Path(self.root) / ".nebuko" / "agents":
            return None
        return self.agents.get(candidate.name)

    def to_env(self) -> str:
        return json.dumps(
            {
                "root": self.root,
                "agents": dict(self.agents),
                "config_name": self.config_name,
                "layer": self.layer,
                "secret_names": list(self.secret_names),
                "manifest_ignored": list(self.manifest_ignored),
                "note": self.note,
            },
            sort_keys=True,
        )

    @classmethod
    def from_env(cls, raw: str) -> "PersonalSnapshot":
        data = json.loads(raw)
        return cls(
            root=data["root"],
            agents=dict(data.get("agents") or {}),
            config_name=data.get("config_name"),
            layer=data.get("layer"),
            secret_names=tuple(data.get("secret_names") or ()),
            manifest_ignored=tuple(data.get("manifest_ignored") or ()),
            note=data.get("note"),
        )


_CURRENT_SNAPSHOT: Optional[PersonalSnapshot] = None


def resolve_personal_snapshot(workspace: Path) -> Optional[PersonalSnapshot]:
    """Read the personal manifest and agent configs once; ``None`` outside a personal run or without a checkout."""
    if not personal_run_from_env(os.environ):
        return None
    root = workspace / PERSONAL_CHECKOUT_DIR
    if not root.is_dir():
        return None

    notes: list[str] = []
    config_name: Optional[str] = None
    ignored: tuple[str, ...] = ()
    manifest = root / ".nebuko" / "nebuko.yaml"
    if manifest.is_file():
        try:
            raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise ValueError("must contain a YAML mapping")
            name = raw.get("agent_config")
            config_name = name.strip() if isinstance(name, str) and name.strip() else None
            ignored = tuple(sorted(str(key) for key in raw if key != "agent_config"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            notes.append(f"unreadable personal manifest ({exc})")

    agents: dict[str, str] = {}
    agents_dir = root / ".nebuko" / "agents"
    if agents_dir.is_dir():
        for entry in sorted(agents_dir.iterdir()):
            if not entry.is_file():
                continue
            try:
                agents[entry.name] = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                notes.append(f"unreadable personal agent config '{entry.name}' ({exc})")

    layer: Optional[str] = None
    secret_names: tuple[str, ...] = ()
    if config_name is not None:
        if config_name in agents:
            layer = "personal"
            try:
                secret_names = tuple(required_secret_names_from_text(agents[config_name]))
            except yaml.YAMLError as exc:
                notes.append(f"unreadable personal agent config '{config_name}' ({exc})")
        else:
            layer = "shipped"

    return PersonalSnapshot(
        root=str(root),
        agents=agents,
        config_name=config_name,
        layer=layer,
        secret_names=secret_names,
        manifest_ignored=ignored,
        note="; ".join(notes) or None,
    )


@contextmanager
def personal_snapshot_scope(workspace: Optional[Path]) -> Iterator[None]:
    """Hold one personal snapshot for the run; the single-job loop wraps all its steps in it."""
    global _CURRENT_SNAPSHOT
    previous = _CURRENT_SNAPSHOT
    _CURRENT_SNAPSHOT = resolve_personal_snapshot(workspace) if workspace is not None else None
    try:
        yield
    finally:
        _CURRENT_SNAPSHOT = previous


def current_personal_snapshot() -> Optional[PersonalSnapshot]:
    """The run's snapshot in the engine, or the one the engine handed this child process."""
    if _CURRENT_SNAPSHOT is not None:
        return _CURRENT_SNAPSHOT
    raw = os.environ.get(PERSONAL_SNAPSHOT_ENV)
    return PersonalSnapshot.from_env(raw) if raw else None


def personal_snapshot_child_env() -> dict[str, str]:
    """The env entry that carries the snapshot to an agent child; empty outside a personal run."""
    snapshot = current_personal_snapshot()
    return {PERSONAL_SNAPSHOT_ENV: snapshot.to_env()} if snapshot is not None else {}


def frozen_agent_config_text(path: str | Path) -> Optional[str]:
    """The snapshot's text for ``path`` when a personal snapshot holds it, else ``None``."""
    snapshot = current_personal_snapshot()
    return snapshot.text_for(path) if snapshot is not None else None
