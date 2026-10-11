"""Comment recency-window entity (ADR-0003, issues #683/#844/#872).

Single source of truth for how many recent thread comments the engine injects
into an agent prompt. The size previously lived only in the reusable GitHub
Actions workflow (``history_comment_count``, default 5) and never reached
Python: ``Engine.history_limit`` was not wired, so the whole thread was injected
(issue #844). Owning the default, the env resolution and the normalization here
keeps a "declared but unwired" cap from recurring.

Two distinct bounds are derived from the same declared value (issue #872):

* :meth:`normalized_count` — the legacy guard/history window: **always** a
  positive bound (default 5), used by the loop guard and the raw-history
  fallback. Unchanged so a host that does not export the variable keeps the
  fail-safe behaviour.
* :meth:`render_limit` — the workflow-state thread ledger's render cap: ``None``
  (full relevant flow, the ADR-0003/post-872 default) when the value is unset
  or non-positive, or a positive tail cap of the *relevant* records when set.
  Classification removes engine noise, so "full relevant flow" is what the
  ledger renders; the cap is an explicit opt-down knob.
"""

from __future__ import annotations

from typing import Mapping, Optional

from pydantic import BaseModel, ConfigDict

DEFAULT_HISTORY_COMMENT_COUNT = 5
HISTORY_COMMENT_COUNT_ENV = "HISTORY_COMMENT_COUNT"


class HistoryWindowConfig(BaseModel):
    """Recency-window size for comment injection (ADR-0003, issue #872)."""

    # ``None`` = the variable was not set. Distinct from an explicit ``0`` only
    # in intent; both yield the legacy default window and an unbounded ledger.
    history_comment_count: Optional[int] = None

    model_config = ConfigDict(frozen=True)

    def normalized_count(self) -> int:
        """A usable legacy window size: a non-positive value falls back to the default.

        Normalization guarantees every caller receives a positive bound — no
        declared value yields unbounded injection on the guard/history path.
        """
        if self.history_comment_count is not None and self.history_comment_count > 0:
            return self.history_comment_count
        return DEFAULT_HISTORY_COMMENT_COUNT

    def render_limit(self) -> Optional[int]:
        """The thread-ledger render cap: ``None`` = full relevant flow.

        A positive value caps the ledger to the last N relevant records; unset,
        zero, or a negative value means unbounded (the post-872 default).
        """
        if self.history_comment_count is not None and self.history_comment_count > 0:
            return self.history_comment_count
        return None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "HistoryWindowConfig":
        """Resolve the window from ``HISTORY_COMMENT_COUNT``.

        Absent, empty, or unparseable input yields the default instance; a
        parseable integer is kept verbatim (the two derived bounds interpret it).
        """
        raw = (env.get(HISTORY_COMMENT_COUNT_ENV) or "").strip()
        if not raw:
            return cls()
        try:
            count = int(raw)
        except (TypeError, ValueError):
            return cls()
        return cls(history_comment_count=count)
