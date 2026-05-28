"""Swappable market-data providers.

A provider knows how to fetch a raw price series for a symbol. The service
layer (``marketdata.service``) maps canonical ranges to provider params,
normalizes the output and caches it — providers stay dumb and stateless so
swapping Twelve Data for Finnhub later is a one-line change in ``get_provider``.

Canonical raw point returned by every provider (chronological ascending):

    {"t": "<iso8601>", "open": float, "high": float,
     "low": float, "close": float, "volume": float | None}

Never log the API key. Requests carry it as a query param; logging only ever
records symbol / interval / status.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Protocol

import httpx
from django.conf import settings

from .exceptions import (
    MarketDataAuthError,
    MarketDataConfigError,
    MarketDataError,
    MarketDataNotFoundError,
    MarketDataRateLimitError,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_ATTEMPTS = 4
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0


class PriceProvider(Protocol):
    """Minimal interface every market-data provider must satisfy."""

    name: str

    def fetch_series(
        self, symbol: str, *, interval: str, outputsize: int
    ) -> list[dict[str, Any]]:
        """Return a chronological-ascending list of canonical raw points."""
        ...


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TwelveDataProvider:
    """Provider backed by Twelve Data's ``/time_series`` endpoint.

    Twelve Data signals business errors (bad symbol, quota) inside a **200**
    body as ``{"status": "error", "code": ..., "message": ...}``, so we never
    trust the HTTP status alone.
    """

    name = "twelvedata"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.TWELVE_DATA_API_KEY
        self._base_url = (base_url or settings.TWELVE_DATA_BASE_URL).rstrip("/")
        self._timeout = timeout

    def fetch_series(
        self, symbol: str, *, interval: str, outputsize: int
    ) -> list[dict[str, Any]]:
        if not self._api_key:
            raise MarketDataConfigError(
                "TWELVE_DATA_API_KEY no está configurada; no puedo obtener histórico de precios."
            )

        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "order": "asc",
            "apikey": self._api_key,
        }
        payload = self._request("/time_series", params=params)
        return self._normalize(payload)

    def _request(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        # Params logged WITHOUT the apikey.
        loggable = {k: v for k, v in params.items() if k != "apikey"}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(url, params=params)
            except httpx.RequestError as exc:
                if attempt == MAX_ATTEMPTS:
                    logger.error(
                        "marketdata transport error",
                        extra={"path": path, "params": loggable, "attempt": attempt, "error": str(exc)},
                    )
                    raise MarketDataError(f"Transport error after {attempt} attempts: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                self._sleep_for_retry_after(response, attempt)
                continue
            if 500 <= response.status_code < 600 and attempt < MAX_ATTEMPTS:
                self._sleep_backoff(attempt)
                continue

            logger.info(
                "marketdata request",
                extra={"path": path, "params": loggable, "status": response.status_code, "attempt": attempt},
            )
            return self._parse(response)

        raise MarketDataError("Unreachable retry path")

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json() if response.content else {}
        except ValueError:
            raise MarketDataError(
                f"Respuesta no-JSON del proveedor (status {response.status_code})",
                status=response.status_code,
            )

        # Twelve Data error body (can arrive with HTTP 200 or 4xx).
        if isinstance(body, dict) and body.get("status") == "error":
            raise self._error_for_body(body)

        if response.status_code >= 400:
            raise self._error_for_body(body if isinstance(body, dict) else {}, http_status=response.status_code)

        return body if isinstance(body, dict) else {}

    def _error_for_body(self, body: dict[str, Any], *, http_status: int | None = None) -> MarketDataError:
        code = body.get("code") or http_status
        message = body.get("message") or f"Provider error (code {code})"
        lowered = message.lower()
        if code in (401, 403) or "api key" in lowered or "apikey" in lowered:
            return MarketDataAuthError(message, status=code, payload=body)
        if code == 429 or "limit" in lowered or "credits" in lowered:
            return MarketDataRateLimitError(message, status=code, payload=body)
        if code == 404 or "not found" in lowered or "symbol" in lowered:
            return MarketDataNotFoundError(message, status=code, payload=body)
        return MarketDataError(message, status=code, payload=body)

    def _normalize(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        values = payload.get("values") or []
        if not isinstance(values, list):
            return []
        points: list[dict[str, Any]] = []
        for v in values:
            if not isinstance(v, dict):
                continue
            close = _to_float(v.get("close"))
            if close is None:
                continue
            points.append({
                "t": v.get("datetime"),
                "open": _to_float(v.get("open")),
                "high": _to_float(v.get("high")),
                "low": _to_float(v.get("low")),
                "close": close,
                "volume": _to_float(v.get("volume")),
            })
        # ``order=asc`` already returns chronological, but guard anyway.
        points.sort(key=lambda p: p["t"] or "")
        return points

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1)))
        delay += random.uniform(0, BACKOFF_BASE)
        time.sleep(delay)

    def _sleep_for_retry_after(self, response: httpx.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = BACKOFF_BASE * (2 ** (attempt - 1))
            time.sleep(min(delay, BACKOFF_CAP))
            return
        self._sleep_backoff(attempt)


def get_provider() -> PriceProvider:
    """Return the active market-data provider.

    Single swap point: replace ``TwelveDataProvider`` here to migrate to
    Finnhub or another source without touching the service or its consumers.
    """
    return TwelveDataProvider()
