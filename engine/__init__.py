"""Typed orchestration engine (issue #590, Stage A).

Public surface: :class:`Engine` and the ``[phase:*]`` vocabulary from
``engine.run_loop``, plus the engine vocabulary in ``engine.models``.
"""

from engine import models
from engine.models import CommentEntry, ExitCode, RoutingDecision, RunOutcome, Trigger
from engine.run_loop import PHASES, Engine, main

__all__ = [
    "models",
    "Trigger",
    "CommentEntry",
    "RoutingDecision",
    "ExitCode",
    "RunOutcome",
    "Engine",
    "PHASES",
    "main",
]
