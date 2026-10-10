"""Token-budget / compaction policy for one agent role (issue #851).

Single source of truth for how an agent's context is compacted and how lean its
prompt/tool surface should be. The defaults encode the approved policy: compaction
is a *late* safety valve on the role's own model rather than the SDK preset's early
event-count trigger (80 events), which fires mid-run and breaks the prompt cache.

Consumed by ``run_agent`` when building the conversation's condenser and system
context; the two SDK defaults it deliberately does *not* inherit are documented on
``resolved_max_size`` below.
"""

from typing import Literal

from pydantic import BaseModel

#: SDK ``LLMSummarizingCondenser`` class default. The preset's early 80-event value is
#: deliberately not adopted so small runs never trip the event-count trigger.
DEFAULT_COMPACTION_MAX_SIZE = 240
#: Fraction of the effective input limit at which token-based compaction fires.
COMPACTION_TOKEN_FRACTION = 0.8


class TokenBudgetPolicy(BaseModel):
    """Token-budget and compaction knobs for one agent role (issue #851)."""

    #: Event count at which compaction fires. ``None`` resolves to the SDK class
    #: default (240) rather than the preset's 80.
    compaction_max_size: int | None = None
    #: Input-token count at which compaction fires. ``None`` resolves to
    #: ``COMPACTION_TOKEN_FRACTION`` of the effective input limit — never "unset".
    compaction_max_tokens: int | None = None
    #: Leading events compaction always preserves; matches ``default_condenser``.
    compaction_keep_first: int = 4  # @stub-protected
    #: Summarizer model. ``None`` -> the role's own model (cache-aligned).
    compaction_model: str | None = None
    #: ``"lean"`` trims per-call tool/prompt overhead (issue #851 S3).
    prompt_profile: Literal["full", "lean"] = "full"
    #: Require reconnaissance to be batched into single multi-action turns.
    require_batched_recon: bool = False

    def merged_with(self, override: "TokenBudgetPolicy | None") -> "TokenBudgetPolicy":  # @stub-protected
        """Field-wise merge: fields set in ``override`` win, the rest inherit.

        Uses pydantic ``exclude_unset`` semantics so an override that only names
        one field leaves every other knob at this (base) policy's value.
        ``override=None`` returns ``self`` unchanged.
        """
        if override is None:
            return self
        overridden = override.model_dump(exclude_unset=True)
        if not overridden:
            return self
        return self.model_copy(update=overridden)

    def resolved_max_size(self) -> int:  # @stub-protected
        """Event-count trigger for compaction (``compaction_max_size`` or 240)."""
        if self.compaction_max_size is not None:
            return self.compaction_max_size
        return DEFAULT_COMPACTION_MAX_SIZE

    def resolved_max_tokens(self, effective_input_limit: int) -> int:  # @stub-protected
        """Token trigger for compaction (``compaction_max_tokens`` or 80% of the limit).

        Always returns a number: a ``None`` that would leave the token valve
        "never firing" is a forbidden outcome (issue #851 contract invariant 2).
        """
        if self.compaction_max_tokens is not None:
            return self.compaction_max_tokens
        return int(COMPACTION_TOKEN_FRACTION * effective_input_limit)

    def resolved_compaction_model(self, role_model: str) -> str:  # @stub-protected
        """Summarizer model: ``compaction_model`` or the role's own model.

        Defaulting to the role's model keeps the summary request on the same
        endpoint as the conversation, so the prompt cache is shared.
        """
        if self.compaction_model is not None:
            return self.compaction_model
        return role_model
