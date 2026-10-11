"""Resolve ``{{ <step> }}`` references to step comments (issue #756, ADR-0013).

A declared step output is a *name-only address*: the referenced step's full
posted comment, state-stripped (exactly what agents see). Resolution joins the
persisted locator (``StepOutputRecord``: step -> comment id) with a comment
index (comment id -> agent-facing body); the body is never copied into state.

Two surfaces share this one resolver:

* the deterministic ``inputs`` seam (``input_wiring.materialize``), and
* ``{{ <step> }}`` placeholders inside ``steps[].prompt``.

``render_prompt_refs`` is a single forward pass: a substituted body is never
re-scanned, so a step that quotes ``{{ ... }}`` cannot induce recursion or
injection amplification (Dify parity, hardened).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional

from ...step_output_record import StepOutputRecord

#: ``{{ <step> }}`` inside a step prompt; the group is the referenced step name.
PROMPT_REF_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*}}")


@dataclass(frozen=True)
class ResolvedOutput:
    """Immutable outcome of resolving one step reference."""

    step: str
    ok: bool
    body: Optional[str] = None
    detail: Optional[str] = None


def resolve_step_output(
    records: Mapping[str, StepOutputRecord],
    comment_index: Mapping[str, str],
    step: str,
) -> ResolvedOutput:
    """Resolve ``step`` to its posted comment body, or a degradation reason.

    Args:
        records: Locators keyed by step name (``StepOutputRecord.index``).
        comment_index: Comment id -> agent-facing body (``build_comment_index``).
        step: The referenced step name.

    Returns:
        A :class:`ResolvedOutput`; ``ok`` is False with a ``detail`` when the
        step has no recorded output, no comment id, or its comment is absent
        from the bounded thread read.
    """
    record = records.get(step)
    if record is None:
        return ResolvedOutput(step=step, ok=False, detail="not recorded")
    if not record.comment_id:
        return ResolvedOutput(step=step, ok=False, detail="no comment recorded")
    body = comment_index.get(str(record.comment_id))
    if body is None:
        return ResolvedOutput(step=step, ok=False, detail="comment not found")
    return ResolvedOutput(step=step, ok=True, body=body)


def render_prompt_refs(
    prompt: str,
    resolved: Mapping[str, ResolvedOutput],
) -> str:
    """Substitute ``{{ <step> }}`` references in one forward pass.

    Unresolved references degrade to ``_(unavailable: <step>)_``, mirroring
    ``input_wiring``'s wording. Substituted bodies are never re-scanned.
    """
    if not prompt:
        return prompt

    def _replace(match: re.Match[str]) -> str:
        step = match.group(1)
        result = resolved.get(step)
        if result is not None and result.ok and result.body is not None:
            return result.body
        return f"_(unavailable: {step})_"

    return PROMPT_REF_PATTERN.sub(_replace, prompt)
