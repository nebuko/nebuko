"""Load config feature module."""

import os
import yaml

from ...sub_domains.app_config.app_config import AppConfig
from ...features.resolve_refs.resolve_refs import resolve_refs


def load_config(path: str = "agent-config.yaml") -> AppConfig:
    """Load, resolve, and validate a layered config file.

    ``path`` may be a real file path or a bare config *name* (for example
    ``agent-config.yaml``). A name is resolved through the layered
    ``.nebuko/agents`` → ``defaults/agents`` lookup (issue #741) so callers no
    longer depend on the checkout layout or the cwd.

    Args:
        path: Path to, or name of, the YAML config file.

    Returns:
        A validated AppConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config fails validation or cannot be parsed.
    """
    if not os.path.isfile(path):
        from default_paths import config_path

        path = config_path(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML mapping, got {type(raw).__name__}")

    resolved = resolve_refs(raw)
    return AppConfig.model_validate(resolved)
