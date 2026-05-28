"""Smoke tests for the market-data layer (provider parsing + service logic).

Pure logic — no network, no Redis, no DB. The provider is faked at the
service boundary and the cache is swapped for an in-process LocMemCache.

    python -m marketdata.tests.test_marketdata_smoke
"""

from __future__ import annotations

import os
import sys

import httpx

failures: list[str] = []


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _expect(cond: bool, label: str) -> None:
    print(f"    {'OK ' if cond else 'FAIL'} — {label}")
    if not cond:
        failures.append(label)


def _expect_raises(fn, exc_type, label: str) -> None:
    try:
        fn()
    except exc_type:
        print(f"    OK  — {label}")
        return
    except Exception as exc:  # wrong exception type
        print(f"    FAIL — {label} (raised {type(exc).__name__})")
        failures.append(label)
        return
    print(f"    FAIL — {label} (did not raise)")
    failures.append(label)


# --- Provider ---------------------------------------------------------------


def _test_provider():
    from marketdata.exceptions import (
        MarketDataAuthError,
        MarketDataConfigError,
        MarketDataNotFoundError,
        MarketDataRateLimitError,
    )
    from marketdata.providers import TwelveDataProvider

    p = TwelveDataProvider(api_key="dummy", base_url="https://example.test")

    print("\n[provider] _normalize sorts asc and floats close")
    raw = {
        "status": "ok",
        "values": [
            {"datetime": "2026-05-02", "open": "2", "high": "3", "low": "1", "close": "2.5", "volume": "10"},
            {"datetime": "2026-05-01", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "9"},
        ],
    }
    points = p._normalize(raw)
    _expect(len(points) == 2, "two points")
    _expect(points[0]["t"] == "2026-05-01", "sorted ascending by datetime")
    _expect(points[0]["close"] == 1.5 and isinstance(points[0]["close"], float), "close coerced to float")

    print("\n[provider] error body inside HTTP 200 maps by code/message")
    _expect_raises(
        lambda: p._parse(httpx.Response(200, json={"status": "error", "code": 429, "message": "API credits limit"})),
        MarketDataRateLimitError,
        "429/limit → rate limit",
    )
    _expect_raises(
        lambda: p._parse(httpx.Response(200, json={"status": "error", "code": 404, "message": "symbol not found"})),
        MarketDataNotFoundError,
        "404/symbol → not found",
    )
    _expect_raises(
        lambda: p._parse(httpx.Response(200, json={"status": "error", "code": 401, "message": "Invalid api key"})),
        MarketDataAuthError,
        "401/api key → auth",
    )

    print("\n[provider] _normalize_batch handles multi-symbol + per-symbol error")
    batch_body = {
        "AAPL": {"status": "ok", "values": [{"datetime": "2026-05-01", "close": "10"}]},
        "MSFT": {"status": "ok", "values": [{"datetime": "2026-05-01", "close": "20"}]},
        "BAD": {"status": "error", "code": 404, "message": "symbol not found"},
    }
    batch = p._normalize_batch(batch_body, ["AAPL", "MSFT", "BAD"])
    _expect(set(batch) == {"AAPL", "MSFT"}, "ok symbols kept, error symbol dropped")
    _expect(batch["AAPL"][0]["close"] == 10.0, "batch close coerced")
    single = p._normalize_batch({"status": "ok", "values": [{"datetime": "2026-05-01", "close": "5"}]}, ["AAPL"])
    _expect(single["AAPL"][0]["close"] == 5.0, "single-symbol flat shape handled")

    print("\n[provider] ok body passes through; missing key → config error")
    parsed = p._parse(httpx.Response(200, json={"status": "ok", "values": []}))
    _expect(parsed.get("status") == "ok", "ok body returned as dict")
    _expect_raises(
        lambda: TwelveDataProvider(api_key="").fetch_series("NVDA", interval="1day", outputsize=30),
        MarketDataConfigError,
        "empty api key → config error",
    )


# --- Service ----------------------------------------------------------------


class _FakeProvider:
    name = "fake"

    def __init__(self, points=None, exc=None):
        self._points = points or []
        self._exc = exc
        self.calls = 0
        self.batch_calls = 0
        self.last_kwargs = None

    def fetch_series(self, symbol, *, interval, outputsize):
        self.calls += 1
        self.last_kwargs = {"symbol": symbol, "interval": interval, "outputsize": outputsize}
        if self._exc is not None:
            raise self._exc
        return list(self._points)

    def fetch_series_batch(self, symbols, *, interval, outputsize):
        self.batch_calls += 1
        if self._exc is not None:
            raise self._exc
        return {s: list(self._points) for s in symbols}


def _series(closes):
    return [
        {"t": f"2026-05-{i + 1:02d}", "open": c, "high": c, "low": c, "close": float(c), "volume": None}
        for i, c in enumerate(closes)
    ]


def _test_service():
    from django.core.cache.backends.locmem import LocMemCache

    from marketdata import service
    from marketdata.exceptions import MarketDataError, MarketDataNotFoundError

    # Swap the dedicated Redis cache for an in-process one.
    shared = LocMemCache("md-test", {})
    shared.clear()
    service._cache = lambda: shared  # type: ignore[assignment]

    print("\n[service] summary math (current/change/high/low/trend)")
    prov = _FakeProvider(points=_series([100, 90, 110]))
    payload = service.get_price_history("nvda", "1m", provider=prov)
    s = payload["summary"]
    _expect(payload["symbol"] == "NVDA", "symbol upper-cased")
    _expect(s["current"] == 110.0, "current = last close")
    _expect(s["change_abs"] == 10.0, "change_abs = last - first")
    _expect(s["change_pct"] == 10.0, "change_pct = 10%")
    _expect(s["high"] == 110.0 and s["low"] == 90.0, "high/low over window")
    _expect(s["trend"] == "alcista", "trend alcista (>+2%)")
    _expect(payload["stale"] is False, "fresh payload not stale")

    print("\n[service] range → provider params mapping")
    _expect(prov.last_kwargs["interval"] == "1day" and prov.last_kwargs["outputsize"] == 30, "1m → (1day, 30)")
    prov_1d = _FakeProvider(points=_series([10, 11]))
    service.get_price_history("AAA", "1d", provider=prov_1d)
    _expect(prov_1d.last_kwargs["interval"] == "5min", "1d → 5min interval")

    print("\n[service] cache hit avoids a second provider call")
    prov2 = _FakeProvider(points=_series([5, 6]))
    service.get_price_history("MSFT", "1m", provider=prov2)
    service.get_price_history("MSFT", "1m", provider=prov2)
    _expect(prov2.calls == 1, "provider called once across two reads")

    print("\n[service] last-good fallback returns stale on provider failure")
    good = _FakeProvider(points=_series([50, 55]))
    service.get_price_history("TSLA", "3m", provider=good)  # populates live + lastgood
    shared.delete(service._live_key("TSLA", "3m"))  # expire the live entry
    failing = _FakeProvider(exc=MarketDataError("boom"))
    fallback = service.get_price_history("TSLA", "3m", provider=failing)
    _expect(fallback["stale"] is True, "stale=True on fallback")
    _expect(fallback["summary"]["current"] == 55.0, "served last-good data")

    print("\n[service] empty series → not found; bad range → ValueError")
    _expect_raises(
        lambda: service.get_price_history("EMPTY", "1m", provider=_FakeProvider(points=[])),
        MarketDataNotFoundError,
        "empty provider series → not found",
    )
    _expect_raises(
        lambda: service.get_price_history("NVDA", "bogus", provider=good),
        ValueError,
        "invalid range → ValueError",
    )


def _test_sparklines():
    from django.core.cache.backends.locmem import LocMemCache

    from marketdata import service

    shared = LocMemCache("md-spark-test", {})
    shared.clear()
    service._cache = lambda: shared  # type: ignore[assignment]

    print("\n[sparklines] returns prices + change_pct per symbol, downsampled")
    prov = _FakeProvider(points=_series(list(range(100, 140))))  # 40 points
    out = service.get_sparklines(["nvda", "msft"], "1d", provider=prov)
    _expect(set(out) == {"NVDA", "MSFT"}, "both symbols present (upper-cased)")
    _expect(out["NVDA"] is not None and "prices" in out["NVDA"], "has prices")
    _expect(len(out["NVDA"]["prices"]) <= service._SPARK_POINTS, "downsampled to <= cap")
    _expect(out["NVDA"]["prices"][-1] == 139.0, "anchors latest price")
    _expect(out["NVDA"]["change_pct"] == round((139 - 100) / 100 * 100, 2), "change_pct over window")

    print("\n[sparklines] cache hit avoids a second batch call")
    before = prov.batch_calls
    service.get_sparklines(["NVDA", "MSFT"], "1d", provider=prov)
    _expect(prov.batch_calls == before, "no new batch call on cached symbols")

    print("\n[sparklines] provider failure → None, no raise")
    failing = _FakeProvider(exc=__import__("marketdata.exceptions", fromlist=["MarketDataError"]).MarketDataError("boom"))
    out2 = service.get_sparklines(["FAIL1", "FAIL2"], "1d", provider=failing)
    _expect(out2["FAIL1"] is None and out2["FAIL2"] is None, "failed symbols → None")


def _run() -> int:
    _test_provider()
    _test_service()
    _test_sparklines()
    if failures:
        print(f"\nMARKETDATA SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nMARKETDATA SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
