"""Script-step outcome report entity (issue #885).

A deterministic ``script`` step's output *is* the comment it posts (ADR-0013),
exactly like an agent step's. This value object renders that comment body from
one execution's captured result: a succeeded/failed banner plus a collapsed,
fenced copy of the command output. It never echoes the command line — script
args/env may carry secrets and the repo has no redaction utility — and it
truncates oversized output to stay under GitHub's comment-size limit.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from ..brand import brand

# GitHub caps a comment at 65,536 characters; the state block and framing add a
# little on top, so the raw output is capped well below the limit. Head+tail is
# kept (with a marker) because a failure's cause is often at either end.
MAX_OUTPUT_CHARS = 30_000

_EMPTY_OUTPUT = "_(no output)_"


def _truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cap ``text`` at ``limit`` chars, keeping the head and tail."""
    if len(text) <= limit:
        return text
    half = limit // 2
    removed = len(text) - 2 * half
    marker = f"\n… [{removed} characters truncated] …\n"
    return text[:half] + marker + text[-half:]


def _code_fence(text: str) -> str:
    """A backtick fence longer than any run inside ``text`` (collision-proof)."""
    longest = max((len(match) for match in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


class ScriptStepReport(BaseModel):
    """One script execution's outcome, plus its canonical rendering.

    Attributes:
        step: Workflow step name that ran the command.
        ok: Whether the command exited successfully.
        exit_code: Process exit code, or ``None`` when it was killed by timeout.
        duration_seconds: Wall-clock execution time.
        output: Captured combined stdout/stderr (as-is).
    """

    step: str
    ok: bool
    exit_code: int | None = None
    duration_seconds: float = 0.0
    output: str = ""

    # Frozen: a report describes one already-finished execution (functional-first
    # value object).
    model_config = ConfigDict(frozen=True)

    def render(self) -> str:
        """Canonical Markdown body posted as the step's output comment.

        The banner carries the step name, exit code, and duration — never the
        command line. The command output is wrapped in a collision-proof fenced
        code block inside a collapsed ``<details>`` section, so trigger-like
        strings in the output stay inert and the thread stays scannable.
        """
        return "\n".join(
            [
                self._banner(),
                "",
                "<details>",
                "<summary>command output</summary>",
                "",
                self._output_block(),
                "",
                "</details>",
            ]
        )

    def _banner(self) -> str:
        status = "succeeded" if self.ok else "failed"
        parts = []
        if self.exit_code is not None:
            parts.append(f"exit code {self.exit_code}")
        parts.append(f"{self.duration_seconds:.1f}s")
        return (
            f"{brand().logo_mark()} **{self.step} command {status}** "
            f"({', '.join(parts)})"
        )

    def _output_block(self) -> str:
        text = self.output if self.output.strip() else _EMPTY_OUTPUT
        fence = _code_fence(text)
        return f"{fence}\n{_truncate_output(text)}\n{fence}"
