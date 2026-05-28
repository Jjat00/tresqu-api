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


def _cache():
    """Return the dedicated market-data cache, falling back to default."""
    try:
        return caches["marketdata"]
    except InvalidCacheBackendError:
        return caches["default"]


def _cache_get(key: str):
    """Read from the market-data cache, degrading to a miss on any backend error.

    The dedicated cache points at Redis; if that Redis is unreachable or
    misconfigured (e.g. ``MARKETDATA_CACHE_URL`` unset in prod) we treat it as
    a cache miss instead of letting a ConnectionError bubble up as a 500.
    """
    try:
        return _cache().get(key)
    except Exception as exc:  # noqa: BLE001 — cache must never break the request
        logger.warning("marketdata cache get failed (%s); treating as miss", exc)
        return None


def _cache_set(key: str, value, ttl: int) -> None:
    try:
        _cache().set(key, value, ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("marketdata cache set failed (%s); skipping", exc)


def _live_key(symbol: str, range_: str) -> str:
    return f"md:hist:{symbol}:{range_}"


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

    interval, outputsize, ttl = RANGE_PARAMS[range_]

    cached = _cache_get(_live_key(symbol, range_))
    if cached is not None:
        return cached

    active = provider or get_provider()
    try:
        raw_points = active.fetch_series(symbol, interval=interval, outputsize=outputsize)
    except MarketDataError as exc:
        last_good = _cache_get(_lastgood_key(symbol, range_))
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
    _cache_set(_live_key(symbol, range_), payload, ttl)
    _cache_set(_lastgood_key(symbol, range_), payload, _LASTGOOD_TTL)
    return payload
