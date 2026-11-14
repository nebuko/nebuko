"""Price subsystem — default model pricing (issue #714 / #721).

Mirrors the ``config/`` exemplar: ``sub_domains/`` holds pydantic entities and
``features/`` holds the I/O and precedence use-cases.
"""

from .sub_domains.price_table.price_table import (
    TOKENS_PER_PRICE_UNIT,
    ModelPrice,
    PriceTable,
    normalize_model_id,
)
from .features.load_price_table.load_price_table import load_price_table
from .features.resolve_model_rates.resolve_model_rates import resolve_model_rates

__all__ = [
    "TOKENS_PER_PRICE_UNIT",
    "ModelPrice",
    "PriceTable",
    "normalize_model_id",
    "load_price_table",
    "resolve_model_rates",
]
