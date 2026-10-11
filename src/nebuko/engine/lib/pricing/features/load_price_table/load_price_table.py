"""Load price table feature — OpenRouter fetch with a file cache (issue #714).

Fully fail-open: pricing metadata is a *bonus* for the metrics report, never a
reason to abort a run. A fresh cache avoids the network; a fetch failure falls
back to a stale cache and, with no cache at all, to ``None`` (the caller then
reports ``$0`` as before).
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlopen

from openhands.sdk import get_logger

from pricing.sub_domains.price_table.price_table import PriceTable

logger = get_logger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

#: 24 hours: the price table changes slowly and OpenRouter caches it 120s anyway.
DEFAULT_TTL_SECONDS = 86_400

#: Small timeout: a slow price service must never delay agent startup.
FETCH_TIMEOUT_S = 5

_DEFAULT_CACHE_DIR = Path.home() / ".nebuko" / "cache" / "prices"
_CACHE_ENV = "NEBUKO_PRICE_CACHE_DIR"
_CACHE_FILENAME = "openrouter.json"


@dataclass(frozen=True)
class _CacheEntry:
    """A parsed cache file: when it was written and the table it holds."""

    fetched_at: float
    table: PriceTable


def _resolve_cache_dir(cache_dir: Optional[Path]) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(os.environ.get(_CACHE_ENV, str(_DEFAULT_CACHE_DIR)))


def _default_fetcher() -> bytes:
    """Fetch the OpenRouter model list as raw bytes."""
    with urlopen(OPENROUTER_MODELS_URL, timeout=FETCH_TIMEOUT_S) as response:
        return response.read()


def _parse(raw: bytes) -> Optional[PriceTable]:
    """Parse a raw OpenRouter payload, or ``None`` on bad JSON/shape."""
    try:
        return PriceTable.from_openrouter(json.loads(raw))
    except (ValueError, TypeError) as exc:
        logger.warning(f"Ignoring unusable price table payload: {exc}")
        return None


def _read_cache(path: Path) -> Optional[_CacheEntry]:
    """Read and parse the cache envelope, or ``None`` when unusable."""
    try:
        envelope = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    table = _parse(json.dumps(envelope.get("payload")).encode())
    if table is None:
        return None
    return _CacheEntry(fetched_at=float(envelope.get("fetched_at", 0.0)), table=table)


def _write_cache(path: Path, fetched_at: float, raw: bytes) -> None:
    """Best-effort cache write — a read-only home must not break pricing."""
    try:
        envelope = {"fetched_at": fetched_at, "payload": json.loads(raw)}
    except (ValueError, TypeError):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope))
    except OSError as exc:
        logger.warning(f"Could not write price cache at {path}: {exc}")


def load_price_table(
    *,
    fetcher: Optional[Callable[[], bytes]] = None,
    cache_dir: Optional[Path] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> Optional[PriceTable]:
    """Return the default price table, from cache when fresh, else the network.

    Args:
        fetcher: Override for the network read (tests inject a stub).
        cache_dir: Override for the cache directory.
        ttl_seconds: Freshness window for the cached table.
        now: Override for the current time (tests).

    Returns:
        A :class:`PriceTable`, or ``None`` when neither a fresh cache nor a
        successful fetch (nor a stale cache) is available.
    """
    path = _resolve_cache_dir(cache_dir) / _CACHE_FILENAME
    current = time.time() if now is None else now

    cached = _read_cache(path)
    if cached is not None and (current - cached.fetched_at) <= ttl_seconds:
        return cached.table

    try:
        raw = (fetcher or _default_fetcher)()
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        logger.warning(f"Price table fetch failed ({exc}); falling back to cache")
        return cached.table if cached is not None else None

    table = _parse(raw)
    if table is None:
        return cached.table if cached is not None else None

    _write_cache(path, current, raw)
    return table
