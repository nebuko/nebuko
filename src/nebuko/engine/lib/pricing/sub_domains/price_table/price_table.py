"""Default model price table (issue #714 / #721).

An immutable snapshot of the OpenRouter price table, used to price models that
no operator-configured ``input_price``/``output_price`` covers — a subscription
provider (CommandCode, OpenCode) reports no per-token cost, so without a default
price source its metrics are structurally ``$0``.

Prices are normalized to **USD per 1M tokens**, matching ``LLMConfig`` units, so
a config price and a table price can be merged per field without unit surgery.
"""

import time
from typing import Optional

from pydantic import BaseModel

#: OpenRouter quotes prices in USD per token; config/table prices are per 1M.
TOKENS_PER_PRICE_UNIT: int = 1_000_000

#: OpenRouter pricing key → our canonical field name.
_OPENROUTER_PRICE_KEYS: dict[str, str] = {
    "prompt": "input_price",
    "completion": "output_price",
    "input_cache_read": "cache_read_price",
    "input_cache_write": "cache_write_price",
}


def _pick(override: Optional[float], base: Optional[float]) -> Optional[float]:
    """The override value when set, else the base value."""
    return override if override is not None else base


def _per_token_to_per_million(value) -> Optional[float]:
    """Convert a per-token USD value to USD per 1M tokens, or ``None``."""
    if value is None:
        return None
    try:
        return float(value) * TOKENS_PER_PRICE_UNIT
    except (TypeError, ValueError):
        return None


def _parse_pricing(pricing: dict) -> "ModelPrice":
    """Map an OpenRouter per-token ``pricing`` map onto per-1M fields."""
    return ModelPrice(
        **{
            field_name: _per_token_to_per_million(pricing.get(source_key))
            for source_key, field_name in _OPENROUTER_PRICE_KEYS.items()
        }
    )


class ModelPrice(BaseModel):
    """A model's four-tier price bundle, in USD per 1M tokens."""

    input_price: Optional[float] = None
    output_price: Optional[float] = None
    cache_read_price: Optional[float] = None
    cache_write_price: Optional[float] = None

    def effective_cache_read_price(self) -> Optional[float]:
        """The cache-read price cost math charges, unset falling back to input.

        Mirrors ``CostRates.from_prices`` so a displayed tier price is exactly
        the rate the estimate used (issue #1048).
        """
        return _pick(self.cache_read_price, self.input_price)

    # @stub-protected: signature defined by architect — do not modify
    def merged_over(self, override: Optional["ModelPrice"]) -> "ModelPrice":
        """Return this price bundle with ``override``'s set fields taking precedence.

        Per-field, not per-bundle: a config that only sets ``input_price`` still
        completes its output/cache tiers from the default table.
        """
        if override is None:
            return self.model_copy()
        return ModelPrice(
            input_price=_pick(override.input_price, self.input_price),
            output_price=_pick(override.output_price, self.output_price),
            cache_read_price=_pick(override.cache_read_price, self.cache_read_price),
            cache_write_price=_pick(override.cache_write_price, self.cache_write_price),
        )


class PriceTable(BaseModel):
    """Immutable snapshot of the default price service, keyed by model id."""

    source: str
    fetched_at: float
    entries: dict[str, ModelPrice]

    # @stub-protected: signature defined by architect — do not modify
    @staticmethod
    def from_openrouter(payload: dict) -> "PriceTable":
        """Parse an OpenRouter ``/models`` payload into a price table."""
        entries: dict[str, ModelPrice] = {}
        for model in payload.get("data", []) or []:
            model_id = model.get("id")
            if not model_id:
                continue
            entries[model_id] = _parse_pricing(model.get("pricing") or {})
        return PriceTable(
            source="openrouter",
            fetched_at=time.time(),
            entries=entries,
        )

    # @stub-protected: signature defined by architect — do not modify
    def match(self, model_path: str) -> Optional[ModelPrice]:
        """Look up ``model_path``, trying each normalized candidate id in order."""
        for candidate in normalize_model_id(model_path):
            if candidate in self.entries:
                return self.entries[candidate]
        return self._match_by_basename(model_path)

    def _match_by_basename(self, model_path: str) -> Optional[ModelPrice]:
        """Fall back to an unambiguous basename match.

        Some providers route by a path that omits the org segment (OpenCode Go
        ships ``openai/glm-5.3-flash``) while the table keys on the
        org-qualified id (``z-ai/glm-5.3-flash``). When exactly one table entry
        shares the model's basename, it is the model the path refers to; an
        ambiguous basename is treated as unknown (fail-open, never a guess).
        """
        basename = model_path.rsplit("/", 1)[-1]
        hits = [
            price
            for model_id, price in self.entries.items()
            if model_id.rsplit("/", 1)[-1] == basename
        ]
        return hits[0] if len(hits) == 1 else None


# @stub-protected: signature defined by architect — do not modify
def normalize_model_id(model_path: str) -> list[str]:
    """Candidate table ids for a routed model path, most specific first.

    A configured path carries a route prefix that the price services do not
    (``openai/deepseek/deepseek-v4.1-flash`` → ``deepseek/deepseek-v4.1-flash``),
    so the exact id is tried first, then progressively stripped of leading
    path segments.
    """
    segments = model_path.split("/")
    return ["/".join(segments[i:]) for i in range(len(segments))]
