Linter failed on #1106 with this error:

```
0s
Run python -m nebuko.engine lint-config defaults/agents/
Traceback (most recent call last):
  File "<frozen runpy>", line 194, in _run_module_as_main
  File "<frozen runpy>", line 151, in _get_module_details
  File "<frozen runpy>", line 114, in _get_module_details
  File "/home/runner/work/nebuko/nebuko/src/nebuko/engine/__init__.py", line 9, in <module>
    from nebuko.engine.run_loop import PHASES, Engine, main
  File "/home/runner/work/nebuko/nebuko/src/nebuko/engine/run_loop.py", line 30, in <module>
    from nebuko.engine.git_session import GitSessionGuard, GuardResult
  File "/home/runner/work/nebuko/nebuko/src/nebuko/engine/git_session.py", line 33, in <module>
    from openhands.sdk import get_logger
ModuleNotFoundError: No module named 'openhands'
Error: Process completed with exit code 1.
```

We may need to add the module in the default environement?