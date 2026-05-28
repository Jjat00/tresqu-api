"""Endpoint smoke test for AssetPriceHistoryView.

Exercises routing, IsAuthenticated, response serialization and the error
mapping through DRF's request factory — no live server, no Redis, no DB
writes (the provider is faked and the user is an unsaved instance, which
``force_authenticate`` accepts).

    python -m marketdata.tests.test_endpoint_smoke
"""

from __future__ import annotations

import os
import sys

failures: list[str] = []


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _expect(cond: bool, label: str) -> None:
    print(f"    {'OK ' if cond else 'FAIL'} — {label}")
    if not cond:
        failures.append(label)


class _FakeProvider:
    name = "fake"

    def __init__(self, points):
        self._points = points

    def fetch_series(self, symbol, *, interval, outputsize):
        return list(self._points)


def _run() -> int:
    from django.core.cache.backends.locmem import LocMemCache
    from django.urls import resolve
    from rest_framework.test import APIRequestFactory, force_authenticate

    from marketdata import service
    from marketdata.views import AssetPriceHistoryView
    from users.models import User

    print("\n[routing] /api/market/assets/<symbol>/history/ resolves to the view")
    match = resolve("/api/market/assets/NVDA/history/")
    _expect(match.func.cls is AssetPriceHistoryView, "URL resolves to AssetPriceHistoryView")

    service._cache = lambda: LocMemCache("ep-test", {})  # type: ignore[assignment]
    factory = APIRequestFactory()
    view = AssetPriceHistoryView.as_view()
    user = User()  # unsaved instance — no DB write
    user.is_authenticated = True  # custom User isn't an AbstractBaseUser

    print("\n[auth] unauthenticated request is rejected")
    resp = view(factory.get("/api/market/assets/NVDA/history/"), symbol="NVDA")
    _expect(resp.status_code in (401, 403), f"unauth → {resp.status_code}")

    print("\n[200] authenticated request returns canonical payload")
    points = [{"t": "2026-05-01", "open": 1, "high": 1, "low": 1, "close": 100.0, "volume": None},
              {"t": "2026-05-02", "open": 1, "high": 1, "low": 1, "close": 110.0, "volume": None}]
    service.get_provider = lambda: _FakeProvider(points)  # type: ignore[assignment]
    req = factory.get("/api/market/assets/NVDA/history/", {"range": "1m"})
    force_authenticate(req, user=user)
    resp = view(req, symbol="NVDA")
    resp.render() if hasattr(resp, "render") else None
    _expect(resp.status_code == 200, f"status 200 (got {resp.status_code})")
    data = resp.data
    _expect(data["symbol"] == "NVDA" and data["range"] == "1m", "symbol/range echoed")
    _expect(data["summary"]["current"] == 110.0, "summary current present")
    _expect(isinstance(data["points"], list) and len(data["points"]) == 2, "points serialized")

    print("\n[400] invalid range")
    req = factory.get("/api/market/assets/NVDA/history/", {"range": "bogus"})
    force_authenticate(req, user=user)
    resp = view(req, symbol="NVDA")
    _expect(resp.status_code == 400, f"invalid range → {resp.status_code}")

    print("\n[404] empty provider series")
    service.get_provider = lambda: _FakeProvider([])  # type: ignore[assignment]
    req = factory.get("/api/market/assets/ZZZZ/history/", {"range": "1m"})
    force_authenticate(req, user=user)
    resp = view(req, symbol="ZZZZ")
    _expect(resp.status_code == 404, f"no data → {resp.status_code}")

    if failures:
        print(f"\nENDPOINT SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nENDPOINT SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
