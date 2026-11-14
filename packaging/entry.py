"""PyInstaller entry point for the single-file ``nebuko`` release binary.

The binary is the client install path (``gh release download``); it carries the
same CLI as the console script, ``nebuko init`` included.
"""

from nebuko.engine.run_loop import main

raise SystemExit(main())
