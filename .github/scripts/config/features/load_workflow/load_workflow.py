"""Load workflow feature module."""

import os
from pathlib import Path
from typing import Union

import yaml

from ...sub_domains.workflow_definition.workflow_definition import WorkflowDefinition

_WORKFLOW_FILE_SUFFIXES = (".yaml", ".yml")


def is_path_like_reference(reference: str) -> bool:
    """Return True when a workflow reference denotes a path, not a name.

    A reference is path-like when it starts with ``workflows/`` or points at
    an explicit workflow file (ends with ``.yaml``/``.yml``).
    """
    return reference.startswith("workflows/") or reference.lower().endswith(_WORKFLOW_FILE_SUFFIXES)


def is_valid_workflow_reference(reference: str) -> bool:
    """Validate a workflow reference for path-safety.

    Rejects empty, absolute, or traversal (``..`` component) references.
    """
    if not reference or reference.startswith("/"):
        return False
    return ".." not in reference.split("/")


def resolve_workflow_reference(
    reference: str,
    workflows_dir: str,
) -> Union[str, Path]:
    """Resolve a workflow reference to a filesystem path.

    Resolution rules (issue #468):
    - ``workflows/...`` references resolve against ``workflows_dir`` after
      stripping the ``workflows/`` prefix (back-compat for legacy refs).
    - Other path-like references (ending ``.yaml``/``.yml``) are resolved
      relative to the repository root (current working directory).
    - Anything else is a workflow *name* and resolves to
      ``<workflows_dir>/<name>.yaml``.

    Args:
        reference: Workflow name or repo-root-relative path.
        workflows_dir: Path to the ``workflows/`` directory.

    Returns:
        Resolved filesystem path (as str or Path).
    """
    if reference.startswith("workflows/"):
        return Path(workflows_dir) / reference[len("workflows/"):]
    if reference.lower().endswith(_WORKFLOW_FILE_SUFFIXES):
        return reference
    return Path(workflows_dir) / f"{reference}.yaml"


def load_workflow(path: str) -> WorkflowDefinition:
    """Load and validate a workflow definition YAML file.

    Args:
        path: Path to the workflow YAML file.

    Returns:
        A validated WorkflowDefinition instance.

    Raises:
        FileNotFoundError: If the workflow file does not exist.
        ValueError: If the config fails validation or cannot be parsed.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Workflow file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(
            f"Workflow file must contain a YAML mapping, got {type(raw).__name__}"
        )

    return WorkflowDefinition.model_validate(raw)
