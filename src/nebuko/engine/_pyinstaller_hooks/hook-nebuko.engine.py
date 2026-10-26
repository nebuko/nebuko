"""PyInstaller hook for ``nebuko.engine`` (issue #1113).

``nebuko.engine.finalize`` imports the frozen-runtime ``config`` and
``workflow_state`` packages lazily through the path bootstrap in
``nebuko.engine.paths`` (``engine/lib`` is appended to ``sys.path`` at
runtime). Static analysis cannot see through that bootstrap, so the module
graph must declare them here — otherwise the frozen binary crashes with
``ModuleNotFoundError: No module named 'config'``.
"""

import sys
from pathlib import Path

# ``engine/lib`` holds the frozen-runtime packages imported through the path
# bootstrap; put it on the build-time search path so PyInstaller can resolve
# the hidden imports below.
_LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.append(str(_LIB_DIR))

hiddenimports = ["config", "config.sub_domains.workflow_step.output_field", "workflow_state"]
