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


def load_workflow_with_dirs(
    wf_name: str,
    workflows_dir: str,
    local_workflows_dir: str = "",
) -> Path:
    """Resolve a workflow by name via an ordered multi-directory scan.

    The consumer-local workflows directory (``local_workflows_dir``) takes
    precedence over the base ``workflows_dir`` (issue #529: "local workflows
    take precedence over the auto-fetched ones"). The FIRST directory that
    actually contains ``<wf_name>.yaml`` wins; identical paths are scanned
    only once.

    Schema validation happens eagerly on the winning file, so a
    schema-invalid local override still wins — the ``ValueError`` propagates
    to the caller, which must fail loudly instead of silently falling back
    to a built-in of the same name.

    Args:
        wf_name: Workflow name (without ``.yaml`` suffix).
        workflows_dir: Base/built-in workflows directory.
        local_workflows_dir: Optional consumer-local workflows directory.

    Returns:
        Path to the winning workflow YAML file.

    Raises:
        FileNotFoundError: When no candidate directory holds the file; the
            message enumerates every searched path.
        ValueError: When the winning file fails schema validation.
    """
    dirs: list[str] = []
    seen: set[str] = set()
    for d in (local_workflows_dir, workflows_dir):
        if not d:
            continue
        norm = str(Path(d).resolve())
        if norm not in seen:
            seen.add(norm)
            dirs.append(d)

    searched: list[str] = []
    for d in dirs:
        candidate = Path(d) / f"{wf_name}.yaml"
        searched.append(str(candidate))
        if candidate.is_file():
            load_workflow(str(candidate))
            return candidate
    raise FileNotFoundError(
        f"Workflow file not found: '{wf_name}.yaml' (searched: {searched})"
    )
