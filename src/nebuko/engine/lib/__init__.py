"""The Nebuko orchestration library — ``nebuko.engine.lib``.

This package holds the runtime the engine drives: workflow state, configuration
models, the agent entry point and the route resolver.

How to use
----------
Import everything by package name, from the top of the module::

    from nebuko.engine.lib.workflow_state import WorkflowState
    from nebuko.engine.lib.config import AppConfig
    from nebuko.engine.lib.resolve_config import resolve_config

Never extend ``sys.path`` to reach these modules, and never add
``# noqa: E402`` to their imports — they are ordinary package imports.

The co-located tests next to each subpackage run under the standard pytest
configuration (``testpaths`` in ``pyproject.toml``)::

    uv run pytest tests src/nebuko/engine/lib

How it works
------------
Every subpackage is a normal member of the ``nebuko`` package, discovered from
``src/`` by setuptools (``[tool.setuptools.packages.find] where = ["src"]``).
Consequences:

* the host process imports the library with no path setup at all;
* an agent child process does the same — its environment needs no
  ``PYTHONPATH`` entry pointing at this directory;
* type checkers resolve ``nebuko.engine.lib.*`` without ``mypy_path`` or
  ``extraPaths`` configuration;
* PyInstaller sees the imports through static analysis, and the
  ``nebuko.engine.lib.*`` names in the engine hook's ``hiddenimports`` cover the
  modules that are imported dynamically.

Maintenance rule
----------------
Adding a module here needs only an ``__init__.py`` in its directory and a
package-qualified import at the call site. Any ``sys.path`` mutation is caught
by ``tests/test_no_sys_path_hacks.py``.
"""
