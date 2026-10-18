"""Batched terminal tool package (issue #982)."""

from .terminal_batch_tool import (
    BATCH_HEADER,
    TERMINAL_BATCH_DESCRIPTION,
    TerminalBatchAction,
    TerminalBatchExecutor,
    TerminalBatchObservation,
    TerminalBatchTool,
    build_terminal_batch_description,
)

__all__ = [
    "BATCH_HEADER",
    "TERMINAL_BATCH_DESCRIPTION",
    "TerminalBatchAction",
    "TerminalBatchExecutor",
    "TerminalBatchObservation",
    "TerminalBatchTool",
    "build_terminal_batch_description",
]
