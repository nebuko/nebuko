"""Load config feature module."""

import os
import yaml

from ...sub_domains.app_config.app_config import AppConfig
from ...features.resolve_refs.resolve_refs import resolve_refs


def load_config(path: str = "agent-config.yaml") -> AppConfig:
    """Load, resolve, and validate a layered config file.

    Args:
        path: Path to the YAML config file.

    Returns:
        A validated AppConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config fails validation or cannot be parsed.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML mapping, got {type(raw).__name__}")

    resolved = resolve_refs(raw)
    return AppConfig.model_validate(resolved)
