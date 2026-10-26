"""Typed orchestration engine (issue #590, Stage A).

Public surface: :class:`Engine` and the ``[phase:*]`` vocabulary from
``nebuko.engine.run_loop``, plus the engine vocabulary in ``nebuko.engine.models``.
"""

from nebuko.engine import models
from nebuko.engine.models import CommentEntry, ExitCode, RunOutcome, Trigger

# Re-exports resolve lazily (:pep:`562`) so importing a light submodule — e.g.
# ``nebuko.engine.paths`` from packaging tests (issue #1165) — does not drag in
# ``run_loop`` and transitively ``openhands``/``litellm``, whose import chain
# mutates ``sys.path`` (litellm's ``proxy_cli`` appends the cwd).
_EXPORTS: dict[str, str] = {
    "PHASES": ".run_loop",
    "Engine": ".run_loop",
    "main": ".run_loop",
    "Trigger": ".models",
    "CommentEntry": ".models",
    "ExitCode": ".models",
    "RunOutcome": ".models",
}

#: Submodules exposed as attributes of the package without importing them
#: (so touching ``nebuko.engine.paths`` does not import ``run_loop``).
_SUBMODULES: tuple[str, ...] = ("models", "paths", "lib")

__all__ = [
    "models",
    "Trigger",
    "CommentEntry",
    "ExitCode",
    "RunOutcome",
    "Engine",
    "PHASES",
    "main",
]


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        if name in _SUBMODULES:
            from importlib import import_module

            module = import_module(f".{name}", __name__)
            globals()[name] = module  # cache: later lookups bypass __getattr__
            return module
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_path, __name__), name)
    globals()[name] = value  # cache: later lookups bypass __getattr__
    return value
