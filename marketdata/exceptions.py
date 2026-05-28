"""Error taxonomy for the market-data layer.

Mirrors the shape of ``wallbit.client`` errors so views and tools can map
them to HTTP statuses / human text uniformly.
"""

from __future__ import annotations

from typing import Any


class MarketDataError(Exception):
    """Base error for any failure talking to a market-data provider."""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class MarketDataAuthError(MarketDataError):
    """Missing or invalid provider API key."""


class MarketDataNotFoundError(MarketDataError):
    """Unknown symbol / no data for the requested series."""


class MarketDataRateLimitError(MarketDataError):
    """Provider quota or per-minute rate limit exceeded after retries."""


class MarketDataConfigError(MarketDataError):
    """The provider is not configured (e.g. empty API key)."""
