# Engine lib packaging (`nebuko.engine.lib`)

The engine's orchestration runtime — everything under `src/nebuko/engine/lib/` —
is a regular subpackage of the `nebuko` distribution. It is no longer a
"frozen runtime directory" injected into `sys.path` at runtime.

## How to use

Import lib modules through the package, from anywhere:

```python
from nebuko.engine.lib.workflow_state import WorkflowState
from nebuko.engine.lib.config import load_config
import nebuko.engine.lib.agent_metrics
```

Run the full suite (engine tests plus the co-located lib tests) with no
path configuration at all:

```
uv run pytest tests src/nebuko/engine/lib
```

A bare `pytest` also collects the co-located lib tests, because `testpaths`
in `pyproject.toml` includes `src/nebuko/engine/lib`.

You never need `sys.path.insert`, `PYTHONPATH`, or `ensure_import_path` —
not in application code, not in tests, not in conftest files.

## How it works

- `src/nebuko/engine/lib/__init__.py` makes the directory a real subpackage
  of the existing `src` layout. Nothing is installed separately and no files
  moved; the modules simply gained a fully-qualified import root.
- Because the distribution is installed from `src/` (setuptools
  `packages.find where = ["src"]`), `nebuko.engine.lib.*` resolves through the
  normal import machinery. mypy and pyright resolve it natively — no
  `extraPaths`, no stub packages, no path configuration.
- Intra-lib imports are absolute (`from nebuko.engine.lib.X import ...`) so
  lib-internal, engine, adapter and test code all read identically and no
  import order matters.
- `ensure_import_path()` in `nebuko.engine.paths` was **removed**, not
  deprecated: a deprecation would keep two import systems alive. `LIB_DIR`
  stays in `paths.py` because it is still used for on-disk file resolution —
  it is never put on `sys.path`.
- The `src/nebuko/engine/lib` entry in pytest's `pythonpath` was dropped
  (`pythonpath = ["src"]` only); the lib tests run under the standard pytest
  path configuration like every other test.

## Migration notes

If you find a straggler bare import of a lib module, rewrite it mechanically:

```python
# before
from workflow_state import WorkflowState
# after
from nebuko.engine.lib.workflow_state import WorkflowState
```

Invariant: **zero `sys.path.insert` / `sys.path.append` anywhere in `src/`
or `tests/`.** If a test cannot import without touching `sys.path`, the
import path is wrong — fix the import, not the path.
