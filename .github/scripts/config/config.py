"""Config module facade with convenience wrappers and constants.

Following the Package-by-Feature pattern, this module serves as the
secondary facade alongside `__init__.py`. It re-exports key symbols
from features/sub-domains and provides convenience wrapper functions
that delegate to feature modules.

External consumers should prefer importing from the package facade:
    from config import load_config, load_workflow
"""

CONFIG_DEFAULT_PATH = "agent-config.yaml"


def load_config(path: str = CONFIG_DEFAULT_PATH):
    """Load, resolve, and validate a layered config file.

    Convenience wrapper delegating to the feature module.

    Args:
        path: Path to the YAML config file. Defaults to CONFIG_DEFAULT_PATH.

    Returns:
        A validated AppConfig instance.
    """
    from .features.load_config.load_config import load_config as _load_config
    return _load_config(path)


def load_agent_names(path: str = CONFIG_DEFAULT_PATH):
    """Agent names declared in the layered agent config.

    Convenience wrapper delegating to the feature module.

    Args:
        path: Path to the YAML config file. Defaults to CONFIG_DEFAULT_PATH.

    Returns:
        A ``frozenset`` of agent names, falling back to the canonical roster
        when the config cannot be read.
    """
    from .features.load_agent_names.load_agent_names import (
        load_agent_names as _load_agent_names,
    )
    return _load_agent_names(path)


def load_workflow(path: str):
    """Load and validate a workflow definition YAML file.

    Convenience wrapper delegating to the feature module.

    Args:
        path: Path to the workflow YAML file.

    Returns:
        A validated WorkflowDefinition instance.
    """
    from .features.load_workflow.load_workflow import load_workflow as _load_workflow
    return _load_workflow(path)


def resolve_refs(config, root=None):
    """Resolve ``${providers.x}`` and ``${llms.y}`` references recursively.

    Convenience wrapper delegating to the feature module.

    Args:
        config: Config dict, list, or string containing ${ref} references.
        root: Root config dict for resolving references.

    Returns:
        Resolved config object.
    """
    from .features.resolve_refs.resolve_refs import resolve_refs as _resolve_refs
    return _resolve_refs(config, root)
