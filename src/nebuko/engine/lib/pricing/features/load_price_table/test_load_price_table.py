"""Tests for the cached price-table loader (issue #714).

Every test injects the fetcher and a tmp cache directory — no network.
"""

import json

from nebuko.engine.lib.pricing.features.load_price_table.load_price_table import (
    DEFAULT_TTL_SECONDS,
    load_price_table,
)

PAYLOAD = {
    "data": [
        {
            "id": "deepseek/deepseek-v4.1-flash",
            "pricing": {"prompt": "0.0000003", "completion": "0.0000012"},
        }
    ]
}


def _payload_bytes() -> bytes:
    return json.dumps(PAYLOAD).encode()


class _CountingFetcher:
    def __init__(self, data: bytes):
        self.data = data
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        return self.data


class _FailingFetcher:
    def __call__(self) -> bytes:
        raise OSError("network down")


class TestFreshCache:
    def test_fetch_populates_cache_and_parses(self, tmp_path):
        fetcher = _CountingFetcher(_payload_bytes())
        table = load_price_table(fetcher=fetcher, cache_dir=tmp_path, now=1000.0)
        assert table is not None
        assert fetcher.calls == 1
        assert (tmp_path / "openrouter.json").is_file()

    def test_fresh_cache_skips_the_network(self, tmp_path):
        first = _CountingFetcher(_payload_bytes())
        load_price_table(fetcher=first, cache_dir=tmp_path, now=1000.0)

        second = _CountingFetcher(b"should not be read")
        table = load_price_table(fetcher=second, cache_dir=tmp_path, now=1000.0)
        assert second.calls == 0
        assert table is not None
        assert table.match("deepseek/deepseek-v4.1-flash") is not None

    def test_expired_cache_refetches(self, tmp_path):
        first = _CountingFetcher(_payload_bytes())
        load_price_table(fetcher=first, cache_dir=tmp_path, now=1000.0)

        second = _CountingFetcher(_payload_bytes())
        load_price_table(
            fetcher=second,
            cache_dir=tmp_path,
            now=1000.0 + DEFAULT_TTL_SECONDS + 1,
        )
        assert second.calls == 1


class TestFailOpen:
    def test_fetch_failure_without_cache_returns_none(self, tmp_path):
        assert (
            load_price_table(fetcher=_FailingFetcher(), cache_dir=tmp_path) is None
        )

    def test_fetch_failure_serves_a_stale_cache(self, tmp_path):
        good = _CountingFetcher(_payload_bytes())
        load_price_table(fetcher=good, cache_dir=tmp_path, now=1000.0)

        stale = load_price_table(
            fetcher=_FailingFetcher(),
            cache_dir=tmp_path,
            now=1000.0 + DEFAULT_TTL_SECONDS * 10,
        )
        assert stale is not None
        assert stale.match("deepseek/deepseek-v4.1-flash") is not None

    def test_unparseable_payload_returns_none(self, tmp_path):
        fetcher = _CountingFetcher(b"not json")
        assert load_price_table(fetcher=fetcher, cache_dir=tmp_path) is None

    def test_structurally_wrong_envelopes_fail_open(self, tmp_path):
        # Valid JSON but wrong shape must never propagate (issue #714 review):
        # a corrupt cache file must not abort the run before build_output.
        wrong_envelopes = [
            [1, 2, 3],  # envelope is a list
            "hello",  # envelope is a scalar
            {"fetched_at": 1.0},  # payload missing
            {"fetched_at": 1.0, "payload": None},  # payload null
            {"fetched_at": 1.0, "payload": [1, 2, 3]},  # payload a list
            {"fetched_at": 1.0, "payload": {"data": [1, 2]}},  # model not a map
        ]
        for envelope in wrong_envelopes:
            cache = tmp_path / "openrouter.json"
            cache.write_text(json.dumps(envelope))
            table = load_price_table(
                fetcher=_FailingFetcher(), cache_dir=tmp_path, now=1000.0
            )
            assert table is None

    def test_non_numeric_fetched_at_fails_open(self, tmp_path):
        cache = tmp_path / "openrouter.json"
        cache.write_text(json.dumps({"fetched_at": "abc", "payload": PAYLOAD}))
        table = load_price_table(
            fetcher=_FailingFetcher(), cache_dir=tmp_path, now=1000.0
        )
        assert table is None

    def test_unwritable_cache_still_prices(self, tmp_path):
        # Point the cache at a path whose parent is a file → mkdir/write fails.
        blocked = tmp_path / "blocked"
        blocked.write_text("x")
        fetcher = _CountingFetcher(_payload_bytes())
        table = load_price_table(fetcher=fetcher, cache_dir=blocked)
        assert table is not None


class TestCacheDirOverride:
    def test_env_var_pins_the_cache_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NEBUKO_PRICE_CACHE_DIR", str(tmp_path))
        fetcher = _CountingFetcher(_payload_bytes())
        load_price_table(fetcher=fetcher)
        assert (tmp_path / "openrouter.json").is_file()
