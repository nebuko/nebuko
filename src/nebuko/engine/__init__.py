"""Typed orchestration engine (issue #590, Stage A).

Public surface: :class:`Engine` and the ``[phase:*]`` vocabulary from
``nebuko.engine.run_loop``, plus the engine vocabulary in ``nebuko.engine.models``.
"""

from nebuko.engine import models
from nebuko.engine.models import CommentEntry, ExitCode, RunOutcome, Trigger
from nebuko.engine.run_loop import PHASES, Engine, main

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
