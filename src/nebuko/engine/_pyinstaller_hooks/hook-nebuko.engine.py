"""PyInstaller hook for ``nebuko.engine`` (issue #1113, #1165).

``nebuko.engine.finalize`` imports the ``config`` and ``workflow_state``
packages from ``nebuko.engine.lib``. They are ordinary package members, so
static analysis follows them on its own; the ``hiddenimports`` below only cover
the modules reached through dynamic imports.
"""

hiddenimports = [
    "nebuko.engine.lib.config",
    "nebuko.engine.lib.config.sub_domains.workflow_step.output_field",
    "nebuko.engine.lib.workflow_state",
]
