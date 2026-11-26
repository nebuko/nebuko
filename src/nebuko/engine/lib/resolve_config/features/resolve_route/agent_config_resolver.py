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

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Optional

__all__ = [
    "AgentConfigNotFound",
    "AgentConfigResolver",
    "PERSONAL_CHECKOUT_DIR",
    "PERSONAL_REPO_SUFFIX",
    "ResolvedAgentConfig",
    "personal_run_from_env",
    "required_secret_envs",
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
            if bare and candidate.is_file():
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
    """The ``api_token_env`` names a config file requires, sorted and unique."""
    import yaml

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
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
