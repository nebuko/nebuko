"""Resolve model rates feature — per-field config-over-table merge (issue #714).

Pure: no I/O. Config prices always win where set; the default price table fills
the gaps, so a model priced only by the service (a subscription-provider model)
and a model priced only by config both resolve, and a partial config is completed
rather than discarded.
"""

from typing import Optional

from pricing.sub_domains.price_table.price_table import ModelPrice, PriceTable


# @stub-protected: signature defined by architect — do not modify
def resolve_model_rates(
    model_ref,
    table: Optional[PriceTable],
) -> Optional[ModelPrice]:
    """Merge configured prices with the default table for ``model_ref``.

    Per-field precedence: ``model_ref.<price>`` when set, else the table's value
    for the same tier. Returns ``None`` unless *both* ``input_price`` and
    ``output_price`` resolve — a rate bundle with an unpriced input or output
    tier cannot produce a trustworthy session cost.
    """
    config_price = ModelPrice(
        input_price=getattr(model_ref, "input_price", None),
        output_price=getattr(model_ref, "output_price", None),
        cache_read_price=getattr(model_ref, "cache_read_price", None),
        cache_write_price=getattr(model_ref, "cache_write_price", None),
    )

    table_price = (
        table.match(model_ref.model_path) if table is not None else None
    )
    merged = config_price if table_price is None else table_price.merged_over(config_price)

    if merged.input_price is None or merged.output_price is None:
        return None
    return merged
