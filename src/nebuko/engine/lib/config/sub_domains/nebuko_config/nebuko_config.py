"""Parsed ``.nebuko/nebuko-config.yaml``: caller-owned runtime settings (issue #1094).

Single source of truth for the settings a caller used to pass as reusable-workflow
``with:`` inputs but that belong to the repository, not the workflow file: the
default workflow name and the agent-config path. The file is wholly nebuko-owned,
so the schema is flat (no nested ``nebuko:`` object) and unknown keys are ignored
(``extra="ignore"``) — future settings migrate into it without breaking older
files.

The file is caller-anchored exactly like ``AGENT_CONFIG_PATH`` (issue #930/#1108
rules): an absolute ``NEBUKO_CONFIG_PATH`` is honored as-is, a relative one is
anchored at the caller workspace, and the default location is
``<caller_root>/.nebuko/nebuko-config.yaml``. An absent file yields all-None
defaults; a present but invalid one fails loudly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

import yaml
from pydantic import BaseModel, ConfigDict

#: Environment variable pinning the config file's location (issue #1094).
NEBUKO_CONFIG_PATH_ENV = "NEBUKO_CONFIG_PATH"

#: Default location, relative to the caller workspace.
DEFAULT_NEBUKO_CONFIG_PATH = ".nebuko/nebuko-config.yaml"


def resolve_caller_root(env: Mapping[str, str] | None = None) -> Path:
    """The workspace relative config paths anchor at.

    ``$GITHUB_WORKSPACE`` outranks the current directory when it names an
    existing one: the engine may run the agent child in a temp workdir, while
    the caller's ``.nebuko/`` stays under the checked-out workspace.
    """
    source = os.environ if env is None else env
    workspace = source.get("GITHUB_WORKSPACE")
    if workspace and Path(workspace).is_dir():
        return Path(workspace).resolve()
    return Path.cwd().resolve()


class NebukoRuntimeConfig(BaseModel):
    """Parsed ``.nebuko/nebuko-config.yaml``: caller-owned runtime settings."""

    # Unknown keys are ignored so future settings can be added to the file by
    # newer Nebuko versions without breaking older readers (issue #1094).
    model_config = ConfigDict(extra="ignore")

    default_workflow: str | None = None
    agent_config_path: str | None = None

    @classmethod
    def load(
        cls,
        caller_root: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "NebukoRuntimeConfig":
        """Load the runtime config; an absent file yields the all-None defaults.

        Args:
            caller_root: Workspace relative (non-absolute) paths anchor at.
                Defaults to ``$GITHUB_WORKSPACE`` when it names an existing
                directory, else the current working directory.
            env: Environment to read ``NEBUKO_CONFIG_PATH`` from; defaults to
                ``os.environ``.

        Returns:
            The parsed config, or an all-None instance when the file is absent.

        Raises:
            ValueError: When the file is not a YAML mapping or fails schema
                validation — a present-but-invalid file must fail loudly, never
                silently fall back to the defaults.
        """
        source = os.environ if env is None else env
        root = Path(caller_root) if caller_root is not None else resolve_caller_root(source)
        raw_path = source.get(NEBUKO_CONFIG_PATH_ENV)
        if raw_path:
            path = Path(raw_path)
            if not path.is_absolute():
                path = root / path
        else:
            path = root / DEFAULT_NEBUKO_CONFIG_PATH
        if not path.is_file():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a YAML mapping")
        return cls.model_validate(raw)
