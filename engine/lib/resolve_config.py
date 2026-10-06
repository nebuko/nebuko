#!/usr/bin/env python3
"""Backward-compatible wrapper for the resolve_config package.

The implementation now lives in the ``resolve_config/`` package
(package-by-feature DDD layout, mirroring ``workflow_state/`` and
``config/``). Python resolves the package over this file on import, so
``import resolve_config`` yields the package with its full public API.
This shim only preserves the historical *script* entry point used by CI:

    python3 resolve_config.py --trigger-body - --prompt-base ... --workflows-dir ...
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    from resolve_config.resolve_config import main

    main()
