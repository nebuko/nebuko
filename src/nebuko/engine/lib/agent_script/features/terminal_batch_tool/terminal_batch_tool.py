"""terminal_batch tool — several statements, one model action (issue #982).

The SDK's ``terminal`` tool accepts exactly one top-level statement per call:
``TerminalSession.execute`` splits the input with ``split_bash_commands`` and
returns ``"Cannot execute multiple commands at once."`` for anything longer.
So a model that needs six reads pays six LLM turns — the dominant cost in the
context run measured by issue #962 (129 actions, ~11s each).

This tool keeps that guard intact and removes the turn cost instead: the model
sends a *list* of statements and the executor replays each entry through the
SDK's ``TerminalExecutor``. Every entry is a single statement, so each call
passes the guard, and because one executor owns one persistent session the
shell state (``cd``, exported variables, the active virtualenv) carries across
entries exactly as it would between two ``terminal`` calls.

The description is ours, not the SDK's: ``TerminalTool.create`` hardcodes
``UNIX_TOOL_DESCRIPTION`` ("One command at a time"), which is the text that
counter-incentivises batching.
"""

from __future__ import annotations

import platform
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self

from openhands.sdk import get_logger
from openhands.sdk.llm import TextContent
from openhands.sdk.tool.tool import (
    Action,
    DeclaredResources,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from openhands.tools.terminal import TerminalAction, TerminalExecutor
from openhands.tools.terminal.descriptions import (
    UNIX_TOOL_DESCRIPTION,
    WINDOWS_TOOL_DESCRIPTION,
)
from pydantic import Field, field_validator
from rich.text import Text

from agent_script.features.terminal_batch_tool.command_deny_list import (
    CommandDenyList,
    push_deny_list,
)

if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)

#: The SDK's "one command at a time" clause, and what replaces it here.
_ONE_AT_A_TIME = (
    "* One command at a time: You can only execute one shell command at a time.\n"
    "  If you need to run multiple commands sequentially, use `&&` or `;`."
)
_ONE_STATEMENT_PER_ENTRY = (
    "* One statement per list entry: each entry is validated on its own, so\n"
    "  batch the statements as separate entries instead of one call each."
)


class TerminalBatchAction(Action):
    """Schema for running several single-statement shell commands in order."""

    commands: list[str] = Field(
        description=(
            "Commands to run in order, each ONE statement (a chain joined by "
            "`;`, `&&`, `||`, `|` or `&` is still one statement). A newline "
            "inside an entry is rejected — split it into separate entries. The "
            "entries share one persistent shell, so `cd`, exported variables "
            "and the active virtualenv carry over from entry to entry."
        )
    )
    stop_on_failure: bool = Field(
        default=False,
        description=(
            "If False (default) every command runs even when an earlier one "
            "fails. If True the batch stops at the first non-zero exit code and "
            "the remaining commands are reported as skipped."
        ),
    )
    timeout: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional per-command time limit in seconds, applied to each entry "
            "individually. Omit to use the terminal's default soft timeout."
        ),
    )
    summary: str | None = Field(
        default=None,
        description=(
            "Optional one-line description of the batch, shown in the run log. "
            "Put your narration HERE, never as prose before the tool call."
        ),
    )

    @field_validator("commands", mode="before")
    @classmethod
    def _accept_single_string(cls, value: Any) -> Any:
        """Tolerate ``commands="ls"`` — one string is one entry.

        Pydantic's list validation would otherwise reject the whole call, so
        the model would lose the batch and its turn to a schema error.
        """
        return [value] if isinstance(value, str) else value

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("$ ", style="bold green")
        content.append(f"{len(self.commands)} command(s)", style="white")
        if self.summary:
            content.append(f" — {self.summary}", style="bright_black")
        if self.stop_on_failure:
            content.append(" [stop on failure]", style="cyan")
        return content


class TerminalBatchObservation(Observation):
    """Per-command results, in the order the commands were given."""

    results: list[str] = Field(
        default_factory=list,
        description="Rendered ``[i/N] command`` blocks, one per command.",
    )
    exit_codes: list[int | None] = Field(
        default_factory=list,
        description=(
            "Exit code of each command; ``-1`` means it hit the soft timeout and "
            "is still running, ``None`` means it was skipped."
        ),
    )
    failed_count: int = Field(
        default=0,
        description="How many commands exited non-zero or reported an error.",
    )
    working_dir: str | None = Field(
        default=None,
        description="Working directory the shell is in after the last command.",
    )

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        content = list(super().to_llm_content)
        if self.working_dir:
            content.append(TextContent(text=f"[Current working directory: {self.working_dir}]"))
        return content

    @property
    def visualize(self) -> Text:
        return Text(self.text or "[no output]", style="bold red" if self.is_error else "bold green")


BATCH_HEADER = """Run several shell commands in ONE call, in order, in the same persistent shell.

Use this instead of calling `terminal` once per command: one call with six
commands costs one model turn, six separate `terminal` calls cost six. Batch
every read, check and command you already know you need for the next step
(`ls`, `grep`, `sed -n`, `git status`, `git diff`, test invocations, ...).

Parameters:
- commands: list of shell commands, each ONE statement. A chain joined by `;`,
  `&&`, `||`, `|` or `&` is one statement; a newline is not — put each
  newline-separated statement in its own list entry.
- stop_on_failure: optional, default false. When true the batch stops at the
  first non-zero exit code and the remaining entries are reported as skipped.
- timeout: optional per-command time limit in seconds.
- summary: optional one-line description of the batch. Write your narration in
  this parameter — never as free text before the tool call.

Every entry returns its own exit code and output, labelled `[i/N] <command>`,
and the batch ends with a one-line success/failure summary. The entries share
one persistent shell, so `cd`, exported variables and the active virtualenv
carry over — put a `cd` first and the rest of the batch runs there.

"""

#: ``prompt_profile: lean`` header (issue #851 S3): the parameter contract
#: verbatim, the why-paragraphs dropped. The per-turn fixed block is sent on
#: every call, so on long runs the prose is pure token overhead — the batch
#: histogram of the issue #1114 re-run showed the model already batches by
#: default once the tool exists.
BATCH_HEADER_LEAN = """Run several shell commands in ONE call, in order, in the same persistent shell.

Parameters:
- commands: list of shell commands, each ONE statement. A chain joined by `;`,
  `&&`, `||`, `|` or `&` is one statement; a newline is not — put each
  newline-separated statement in its own list entry.
- stop_on_failure: optional; when true the batch stops at the first non-zero exit.
- timeout: optional per-command limit in seconds. A command exceeding its soft
  timeout keeps running in the background and reports exit code -1.
- summary: optional one-line description; narration belongs here, never as
  prose before the tool call.

Each entry returns its own `[i/N] <command>` block with exit code and output.
The shell (`cd`, exports, virtualenv) persists across entries and calls.
"""

#: Recon directive appended to the tool's prompt guidance when the role's
#: ``token_budget.require_batched_recon`` is on: one read per round-trip is
#: the dominant turn-count cost (74 of 134 batches in the measured run
#: carried 1-2 commands).
RECON_GUIDANCE = """
### Reconnaissance discipline
- Aim for 4+ commands per `terminal_batch` call. Stack every read you already
  know you need (`ls`, `grep`, `sed -n`, `git status`, `git diff`, `wc -l`)
  into ONE call before acting on the results.
- Never send a one-command batch for a read. If you find yourself wanting a
  second look at something, batch it with the checks around it."""



def build_terminal_batch_description(lean: bool = False) -> str:
    """Our batching header followed by the SDK's shell/platform guidance.

    The SDK text carries the rules this tool inherits (soft timeout, background
    commands, output truncation, terminal reset); only its "one command at a
    time" clause is wrong here, so that clause is replaced rather than dropped.

    ``lean=True`` (``prompt_profile: lean``, issue #851 S3) drops the SDK tail:
    the essential contract lives in the header, and the tail's ~2.5K characters
    ride along on every call of a long run.
    """
    if lean:
        return BATCH_HEADER_LEAN
    sdk_text = (
        WINDOWS_TOOL_DESCRIPTION
        if platform.system() == "Windows"
        else UNIX_TOOL_DESCRIPTION
    )
    return BATCH_HEADER + sdk_text.replace(_ONE_AT_A_TIME, _ONE_STATEMENT_PER_ENTRY)


TERMINAL_BATCH_DESCRIPTION = build_terminal_batch_description()


def _has_newline(command: str) -> bool:
    return "\n" in command.strip()


def _render_result(index: int, total: int, command: str, observation) -> str:
    """One ``[i/N]`` block: the command, then the SDK's own rendering."""
    header = f"[{index}/{total}] $ {command}"
    body = observation.text.rstrip("\n")
    return f"{header}\n{body}" if body else header


def _denied_observation(message: str) -> TerminalBatchObservation:
    """A minimal error observation for a denied statement."""
    return TerminalBatchObservation.from_text(text=message, is_error=True)


class TerminalBatchExecutor(ToolExecutor):
    """Replays each command through a lazily built SDK ``TerminalExecutor``.

    The executor is created on the first call, from the live conversation's
    workspace, mirroring ``TerminalTool.create``: at ``create`` time the
    conversation may not be bound yet, and an exception there would break agent
    startup instead of one tool call.
    """

    def __init__(
        self,
        executor: TerminalExecutor | None = None,
        deny_list: CommandDenyList | None = None,
    ) -> None:
        self._executor = executor
        self.deny_list = deny_list if deny_list is not None else push_deny_list()

    def _resolve_executor(self, conversation) -> TerminalExecutor:
        if self._executor is not None:
            return self._executor
        conv_state = getattr(conversation, "state", None)
        workspace = getattr(conv_state, "workspace", None)
        working_dir = getattr(workspace, "working_dir", None)
        if not working_dir:
            raise ValueError("terminal_batch: no workspace working directory")
        self._executor = TerminalExecutor(
            working_dir=working_dir,
            full_output_save_dir=getattr(
                conv_state, "env_observation_persistence_dir", None
            ),
        )
        return self._executor

    def __call__(
        self,
        action: TerminalBatchAction,
        conversation: Any | None = None,
    ) -> TerminalBatchObservation:
        try:
            executor = self._resolve_executor(conversation)
        except Exception as exc:
            logger.warning(f"terminal_batch could not bind a terminal session: {exc}")
            return TerminalBatchObservation.from_text(
                text=f"terminal_batch could not start a shell session: {exc}",
                is_error=True,
            )

        total = len(action.commands)
        results: list[str] = []
        exit_codes: list[int | None] = []
        failed = 0
        stopped = False
        working_dir: str | None = None

        for index, command in enumerate(action.commands, start=1):
            if stopped:
                exit_codes.append(None)
                results.append(f"[{index}/{total}] $ {command}\n(skipped)")
                continue
            if _has_newline(command):
                failed += 1
                exit_codes.append(None)
                results.append(
                    f"[{index}/{total}] $ {command}\n"
                    "(rejected: one statement per entry — split the newlines into "
                    "separate list entries)"
                )
                stopped = action.stop_on_failure
                continue

            denied = self.deny_list.check(command)
            if denied is not None:
                logger.warning(
                    f"terminal_batch: command {index} denied by rule "
                    f"{denied.rule_name}"
                )
                failed += 1
                exit_codes.append(1)
                results.append(
                    _render_result(index, total, command, _denied_observation(denied.message))
                )
                stopped = action.stop_on_failure
                continue

            try:
                observation = executor(
                    TerminalAction(command=command, timeout=action.timeout),
                    conversation,
                )
            except TypeError as exc:
                # The resolved executor itself is not usable (a test double or
                # a broken binding): the command never ran, so this is not a
                # failure of the command — record it as not-executed.
                logger.warning(f"terminal_batch: no usable terminal session: {exc}")
                exit_codes.append(None)
                results.append(f"[{index}/{total}] $ {command}\n(not executed: {exc})")
                continue
            except Exception as exc:
                # One bad entry must not cost the whole batch: the executor
                # raises for cases the SDK does not turn into an error
                # observation (a dead session, a reset/input conflict, ...),
                # and the commands before it already ran and have results.
                logger.warning(f"terminal_batch: command {index} raised: {exc}")
                failed += 1
                exit_codes.append(None)
                results.append(
                    f"[{index}/{total}] $ {command}\n"
                    f"(raised {type(exc).__name__}: {exc})"
                )
                stopped = action.stop_on_failure
                continue

            exit_codes.append(observation.exit_code)
            working_dir = observation.metadata.working_dir or working_dir
            results.append(_render_result(index, total, command, observation))

            # -1 is the SDK's "soft timeout, still running" — not a failure.
            if observation.is_error or (
                observation.exit_code is not None and observation.exit_code > 0
            ):
                failed += 1
                stopped = action.stop_on_failure

        if failed:
            summary = f"{failed}/{total} command(s) failed"
        elif len(exit_codes) == total and None not in exit_codes:
            summary = f"{total}/{total} command(s) succeeded"
        else:
            summary = "batch ended early"
        return TerminalBatchObservation.from_text(
            text="\n\n".join(results) + f"\n\n[{summary}]",
            is_error=failed > 0,
            results=results,
            exit_codes=exit_codes,
            failed_count=failed,
            working_dir=working_dir,
        )


class TerminalBatchTool(ToolDefinition[TerminalBatchAction, TerminalBatchObservation]):
    """Batched shell execution on the terminal's own session (issue #982)."""

    def declared_resources(self, action: Action) -> DeclaredResources:  # noqa: ARG002
        # Mirrors TerminalTool: a pooled (tmux) executor isolates calls by pane,
        # a single-session executor must serialize against `terminal` calls.
        executor = getattr(self.executor, "_executor", None)
        if getattr(executor, "is_pooled", False):
            return DeclaredResources(keys=(), declared=True)
        return DeclaredResources(keys=("terminal:session",), declared=True)

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        *,
        prompt_profile: str = "full",
        require_batched_recon: bool = False,
        **params: Any,
    ) -> Sequence[Self]:
        """Create a TerminalBatchTool with a lazily bound executor.

        The two keyword parameters are the role's ``token_budget`` knobs
        (issue #851), delivered through the ``Tool`` spec's ``params`` by
        ``resolve_toolset``: ``prompt_profile="lean"`` trims the description,
        ``require_batched_recon=True`` appends the batching directive to the
        prompt guidance.
        """
        if params:
            raise ValueError(
                f"TerminalBatchTool doesn't accept parameters: {sorted(params)}"
            )
        if prompt_profile not in ("full", "lean"):
            raise ValueError(f"unknown prompt_profile: {prompt_profile!r}")
        guidance = (
            "Call terminal_batch with JSON arguments only. Any "
            "narration belongs in the `summary` parameter, never in "
            "prose before the call."
        )
        if require_batched_recon:
            guidance += RECON_GUIDANCE
        return [
            cls(
                description=build_terminal_batch_description(
                    lean=prompt_profile == "lean"
                ),
                action_type=TerminalBatchAction,
                observation_type=TerminalBatchObservation,
                executor=TerminalBatchExecutor(),
                prompt_guidance=guidance,
                annotations=ToolAnnotations(
                    title="terminal_batch",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
            )
        ]
