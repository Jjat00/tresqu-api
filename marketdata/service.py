"""Market-data service: ranges → provider params → cached canonical series.

This is the single entry point both the REST endpoint (dashboard chart) and
the analyst agent tool call. It maps a human range to provider params, caches
results per ``(symbol, range)`` to protect the provider quota, computes a
compact summary (current / change / high / low / trend), and falls back to the
last successful response (marked ``stale``) when the provider is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError

from .exceptions import MarketDataError, MarketDataNotFoundError
from .providers import PriceProvider, get_provider

logger = logging.getLogger(__name__)

# Canonical range → (provider interval, outputsize, cache TTL seconds).
# outputsize is sized to cover the window with some headroom.
RANGE_PARAMS: dict[str, tuple[str, int, int]] = {
    "1d": ("5min", 78, 120),
    "1w": ("30min", 140, 300),
    "1m": ("1day", 30, 1800),
    "3m": ("1day", 90, 1800),
    "1y": ("1day", 260, 43200),
    "5y": ("1week", 260, 43200),
    "max": ("1month", 300, 43200),
}

VALID_RANGES = tuple(RANGE_PARAMS.keys())

# Last-good fallback is kept much longer than the live TTL so a quota outage
# still serves a recent (stale) chart instead of an error.
_LASTGOOD_TTL = 7 * 24 * 3600

_TREND_THRESHOLD_PCT = 2.0

# Sparklines for the markets table are cached longer than the live chart TTL
# (they don't need second-level freshness) and the keys are global per symbol,
# so the shared "popular" set costs only a handful of provider credits per
# window regardless of how many users load the table.
_SPARK_TTL = 30 * 60
# How many points to keep in a sparkline (downsampled from the raw series).
_SPARK_POINTS = 24
# Default window powering the table's change% + sparkline (≈ "today").
DEFAULT_SPARK_RANGE = "1d"
# Twelve Data free tier allows a limited number of symbols per batch request.
_BATCH_CHUNK = 8


def _cache():
    """Return the dedicated market-data cache, falling back to default."""
    try:
        return caches["marketdata"]
    except InvalidCacheBackendError:
        return caches["default"]


def _live_key(symbol: str, range_: str) -> str:
    return f"md:hist:{symbol}:{range_}"


def _spark_key(symbol: str, range_: str) -> str:
    return f"md:spark:{symbol}:{range_}"


def _downsample(prices: list[float], target: int) -> list[float]:
    """Evenly subsample ``prices`` to at most ``target`` points, keeping the last."""
    n = len(prices)
    if n <= target:
        return prices
    step = n / target
    picked = [prices[int(i * step)] for i in range(target)]
    picked[-1] = prices[-1]  # always anchor the latest price
    return picked


def _lastgood_key(symbol: str, range_: str) -> str:
    return f"md:hist:lastgood:{symbol}:{range_}"


def _trend(change_pct: float) -> str:
    if change_pct > _TREND_THRESHOLD_PCT:
        return "alcista"
    if change_pct < -_TREND_THRESHOLD_PCT:
        return "bajista"
    return "lateral"


def _summarize(points: list[dict[str, Any]]) -> dict[str, Any]:
    prices = [p["price"] for p in points if p.get("price") is not None]
    if not prices:
        return {
            "current": None, "change_abs": None, "change_pct": None,
            "high": None, "low": None, "trend": "lateral", "points_count": 0,
        }
    first = prices[0]
    current = prices[-1]
    change_abs = current - first
    change_pct = (change_abs / first * 100) if first else 0.0
    return {
        "current": round(current, 4),
        "change_abs": round(change_abs, 4),
        "change_pct": round(change_pct, 2),
        "high": round(max(prices), 4),
        "low": round(min(prices), 4),
        "trend": _trend(change_pct),
        "points_count": len(prices),
    }


def _build_payload(symbol: str, range_: str, raw_points: list[dict[str, Any]]) -> dict[str, Any]:
    points = [
        {"t": p["t"], "price": round(p["close"], 4)}
        for p in raw_points
        if p.get("close") is not None and p.get("t")
    ]
    return {
        "symbol": symbol,
        "range": range_,
        "points": points,
        "summary": _summarize(points),
        "stale": False,
        "source": get_provider().name,
    }


def get_price_history(
    symbol: str,
    range_: str = "1m",
    *,
    provider: PriceProvider | None = None,
) -> dict[str, Any]:
    """Return the canonical price-history payload for ``symbol`` over ``range_``.

    Raises ``ValueError`` for an unknown range. On provider failure, returns
    the last-good cached payload with ``stale=True`` if available; otherwise
    re-raises the ``MarketDataError`` for the caller to surface.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol_required")
    if range_ not in RANGE_PARAMS:
        raise ValueError(f"invalid_range:{range_}")

    cache = _cache()
    interval, outputsize, ttl = RANGE_PARAMS[range_]

    cached = cache.get(_live_key(symbol, range_))
    if cached is not None:
        return cached

    active = provider or get_provider()
    try:
        raw_points = active.fetch_series(symbol, interval=interval, outputsize=outputsize)
    except MarketDataError as exc:
        last_good = cache.get(_lastgood_key(symbol, range_))
        if last_good is not None:
            logger.warning(
                "marketdata: provider failed, serving last-good (stale)",
                extra={"symbol": symbol, "range": range_, "error": str(exc)},
            )
            return {**last_good, "stale": True}
        raise

    payload = _build_payload(symbol, range_, raw_points)
    if not payload["points"]:
        # Provider answered OK but with no usable data (delisted, bad symbol
        # that didn't 404, etc.). Treat as not found so callers show a clear
        # message instead of a summary full of nulls.
        raise MarketDataNotFoundError(f"Sin datos de precio para '{symbol}' ({range_}).")
    cache.set(_live_key(symbol, range_), payload, ttl)
    cache.set(_lastgood_key(symbol, range_), payload, _LASTGOOD_TTL)
    return payload


def _spark_from_points(prices: list[float]) -> dict[str, Any] | None:
    if not prices:
        return None
    first, last = prices[0], prices[-1]
    change_pct = round((last - first) / first * 100, 2) if first else 0.0
    return {
        "prices": _downsample(prices, _SPARK_POINTS),
        "change_pct": change_pct,
        "trend": _trend(change_pct),
    }


def get_sparklines(
    symbols: list[str],
    range_: str = DEFAULT_SPARK_RANGE,
    *,
    provider: PriceProvider | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Return ``{symbol: {prices, change_pct, trend} | None}`` for a markets table.

    Cache-first per symbol (global keys, ~30 min TTL). Cache misses are fetched
    from the provider in batches. Any provider failure (quota/auth/transport)
    degrades to ``None`` for the affected symbols so the table still renders
    from Wallbit data — it never raises except for an invalid range.
    """
    if range_ not in RANGE_PARAMS:
        raise ValueError(f"invalid_range:{range_}")

    cache = _cache()
    interval, outputsize, _ttl = RANGE_PARAMS[range_]

    result: dict[str, dict[str, Any] | None] = {}
    misses: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        sym = (raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        cached = cache.get(_spark_key(sym, range_))
        if cached is not None:
            result[sym] = cached
        else:
            misses.append(sym)

    if not misses:
        return result

    active = provider or get_provider()
    fetched: dict[str, list[dict[str, Any]]] = {}
    try:
        for i in range(0, len(misses), _BATCH_CHUNK):
            chunk = misses[i:i + _BATCH_CHUNK]
            fetched.update(active.fetch_series_batch(chunk, interval=interval, outputsize=outputsize))
    except MarketDataError as exc:
        logger.warning("sparklines batch failed: %s", exc)

    for sym in misses:
        raw_points = fetched.get(sym) or []
        prices = [round(p["close"], 4) for p in raw_points if p.get("close") is not None]
        spark = _spark_from_points(prices)
        result[sym] = spark
        if spark is not None:
            cache.set(_spark_key(sym, range_), spark, _SPARK_TTL)

    return result
