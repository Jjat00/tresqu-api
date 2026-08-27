"""HTTP client for the Wallbit public REST API.

All requests go through :class:`WallbitClient`, which handles authentication,
exponential backoff that honors ``Retry-After``/``X-RateLimit-*`` headers,
and structured logging that never leaks the API key.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

API_PREFIX = "/api/public/v1"
DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_ATTEMPTS = 4
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0


class WallbitError(Exception):
    """Base error for any failure talking to Wallbit."""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class WallbitAuthError(WallbitError):
    """401 — missing or invalid API key."""


class WallbitPermissionError(WallbitError):
    """403 — scope or IP whitelist."""


class WallbitNotFoundError(WallbitError):
    """404."""


class WallbitKYCError(WallbitError):
    """412 — KYC incomplete or account blocked."""


class WallbitValidationError(WallbitError):
    """400 or 422 — invalid parameters / insufficient funds."""


class WallbitRateLimitError(WallbitError):
    """429 — rate limit exceeded (after retries, or immediately when the
    client was built with ``retry_rate_limited=False``).

    ``retry_after`` carries the upstream ``Retry-After`` seconds when present
    so callers can size a cooldown instead of hammering the blocked window.
    """

    retry_after: float | None = None


class WallbitServerError(WallbitError):
    """5xx."""


@dataclass(frozen=True)
class RateLimitState:
    limit: int | None
    remaining: int | None
    reset_at: int | None


@dataclass(frozen=True)
class WallbitResponse:
    status: int
    data: Any
    rate_limit: RateLimitState


class WallbitClient:
    """Thin synchronous client.

    Instances hold an API key in memory. Never log instances, never serialize
    them. Use ``with WallbitClient(key) as client`` to ensure the underlying
    httpx.Client closes.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
        retry_rate_limited: bool = True,
    ) -> None:
        """
        ``max_attempts`` bounds transport/5xx/429 retries. ``retry_rate_limited``
        controls whether a 429 is retried after sleeping ``Retry-After``: keep
        it on for background jobs, turn it OFF on request-path reads — the
        dashboard would otherwise block for up to ``BACKOFF_CAP × attempts``
        seconds while every retry keeps the upstream (Cloudflare) rate-limit
        window open. Callers should fail fast and serve a cached snapshot.
        """
        if not api_key:
            raise WallbitAuthError("API key is required")
        self._api_key = api_key
        self._max_attempts = max(1, int(max_attempts))
        self._retry_rate_limited = retry_rate_limited
        self._client = httpx.Client(
            base_url=(base_url or settings.WALLBIT_API_BASE_URL).rstrip("/"),
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "Tresqu/1.0 (+https://tresqu.com)",
            },
        )

    def __enter__(self) -> "WallbitClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(self, path: str, *, params: dict | None = None) -> WallbitResponse:
        return self._request("GET", path, params=params)

    def post(self, path: str, *, json: dict | None = None) -> WallbitResponse:
        return self._request("POST", path, json=json)

    def patch(self, path: str, *, json: dict | None = None) -> WallbitResponse:
        return self._request("PATCH", path, json=json)

    def delete(self, path: str) -> WallbitResponse:
        return self._request("DELETE", path)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> WallbitResponse:
        url = f"{API_PREFIX}{path}" if not path.startswith(API_PREFIX) else path
        headers = {"X-API-Key": self._api_key}

        max_attempts = self._max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.request(
                    method, url, params=params, json=json, headers=headers
                )
            except httpx.RequestError as exc:
                if attempt == max_attempts:
                    logger.error(
                        "wallbit transport error", extra={"method": method, "path": url, "attempt": attempt, "error": str(exc)}
                    )
                    raise WallbitError(f"Transport error after {attempt} attempts: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            rate_limit = _parse_rate_limit(response.headers)
            logger.info(
                "wallbit request",
                extra={
                    "method": method,
                    "path": url,
                    "status": response.status_code,
                    "attempt": attempt,
                    "rate_remaining": rate_limit.remaining,
                },
            )

            if response.status_code == 429 and self._retry_rate_limited and attempt < max_attempts:
                self._sleep_for_retry_after(response, attempt)
                continue

            if 500 <= response.status_code < 600 and attempt < max_attempts:
                self._sleep_backoff(attempt)
                continue

            return self._build_response(response, rate_limit)

        raise WallbitError("Unreachable retry path")

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

    def _build_response(self, response: httpx.Response, rate_limit: RateLimitState) -> WallbitResponse:
        payload: Any
        try:
            payload = response.json() if response.content else None
        except ValueError:
            payload = response.text

        status = response.status_code
        if status >= 400:
            error = _error_for_status(status, payload)
            if isinstance(error, WallbitRateLimitError):
                error.retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise error

        return WallbitResponse(status=status, data=payload, rate_limit=rate_limit)


def _parse_rate_limit(headers) -> RateLimitState:
    def _int(name: str) -> int | None:
        value = headers.get(name)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return RateLimitState(
        limit=_int("X-RateLimit-Limit"),
        remaining=_int("X-RateLimit-Remaining"),
        reset_at=_int("X-RateLimit-Reset"),
    )


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _error_for_status(status: int, payload: Any) -> WallbitError:
    message = _extract_message(payload) or f"Wallbit responded with status {status}"
    if status == 401:
        return WallbitAuthError(message, status=status, payload=payload)
    if status == 403:
        return WallbitPermissionError(message, status=status, payload=payload)
    if status == 404:
        return WallbitNotFoundError(message, status=status, payload=payload)
    if status == 412:
        return WallbitKYCError(message, status=status, payload=payload)
    if status in (400, 422):
        return WallbitValidationError(message, status=status, payload=payload)
    if status == 429:
        return WallbitRateLimitError(message, status=status, payload=payload)
    if 500 <= status < 600:
        return WallbitServerError(message, status=status, payload=payload)
    return WallbitError(message, status=status, payload=payload)


def _extract_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return None
