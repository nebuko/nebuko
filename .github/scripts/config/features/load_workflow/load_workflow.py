"""Load workflow feature module."""

import os
import yaml

from ...sub_domains.workflow_definition.workflow_definition import WorkflowDefinition


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
