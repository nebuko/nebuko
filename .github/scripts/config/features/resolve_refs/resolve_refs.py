"""${ref} resolver feature module."""

import re
from typing import Any

REF_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_.\-]*)\}")


def resolve_refs(config: dict | list | str, root: dict | None = None) -> Any:
    """Resolve ``${providers.x}`` and ``${llms.y}`` references recursively.

    * Full-string match (e.g. ``"${providers.openrouter}"``) → returns the
      referenced object (dict) directly.
    * Partial inline substitution (e.g. ``"prefix_${llms.x.model_path}_suffix"``)
      → substitutes string values only.
    """
    if root is None:
        root = config

    if isinstance(config, dict):
        return {k: resolve_refs(v, root) for k, v in config.items()}

    if isinstance(config, list):
        return [resolve_refs(item, root) for item in config]

    if isinstance(config, str):
        # Full-string match → return the resolved object (recursively)
        if REF_PATTERN.fullmatch(config):
            parts = REF_PATTERN.fullmatch(config).group(1).split(".")
            obj = root
            for part in parts:
                obj = obj[part]
            return resolve_refs(obj, root)

        # Partial inline substitution
        def _replacer(match: re.Match) -> str:
            parts = match.group(1).split(".")
            obj = root
            for part in parts:
                obj = obj[part]
            return str(obj) if not isinstance(obj, (dict, list)) else match.group(0)

        return REF_PATTERN.sub(_replacer, config)

    return config
