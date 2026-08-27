"""Smoke tests for the shared live snapshot (``wallbit.portfolio.get_live_snapshot``).

Pure logic — no network, no Redis, no DB. The Wallbit client is faked at the
module boundary and the cache is swapped for an in-process LocMemCache.

Covers the failure that blanked the dashboard on 2026-08-27: Wallbit's
Cloudflare rate limit tripped by the dashboard's own burst, with every retry
keeping the block alive. The snapshot must (1) fetch once and share, (2) fail
fast on 429 and serve last-good marked stale, (3) stay away from Wallbit
during the cooldown, (4) single-flight concurrent callers, and (5) refuse to
report an empty portfolio when there is nothing to serve.

    python -m wallbit.tests.test_portfolio_snapshot_smoke
"""

from __future__ import annotations

import os
import sys
import threading
import time
from types import SimpleNamespace

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


def _expect_raises(fn, exc_type, label: str):
    try:
        fn()
    except exc_type:
        print(f"    OK  — {label}")
        return
    except Exception as exc:  # wrong exception type
        print(f"    FAIL — {label} (raised {type(exc).__name__}: {exc})")
        failures.append(label)
        return
    print(f"    FAIL — {label} (did not raise)")
    failures.append(label)


# --- Fake Wallbit client ------------------------------------------------------

_STOCKS = {"data": [
    {"symbol": "USD", "shares": "12.5"},
    {"symbol": "NVDA", "shares": "0.05"},
    {"symbol": "SPCX", "shares": "1"},
]}
_CHECKING = {"data": [{"currency": "USD", "balance": "40.00"}]}
_ASSETS = {
    "NVDA": {"data": {"symbol": "NVDA", "name": "NVIDIA", "type": "STOCK", "price": "180.0"}},
    "SPCX": {"data": {"symbol": "SPCX", "name": "SpaceX", "type": "STOCK", "price": "50.0"}},
}


class _FakeClient:
    """Stands in for ``WallbitClient`` inside ``wallbit.portfolio``."""

    calls: list[str] = []
    init_kwargs: list[dict] = []
    mode = "ok"  # ok | rate_limited | server_error | slow

    def __init__(self, api_key, **kwargs):
        _FakeClient.init_kwargs.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def get(self, path, *, params=None):
        from wallbit.client import WallbitRateLimitError, WallbitServerError

        _FakeClient.calls.append(path)
        if _FakeClient.mode == "rate_limited":
            err = WallbitRateLimitError("You are being rate-limited", status=429)
            err.retry_after = 42.0
            raise err
        if _FakeClient.mode == "server_error":
            raise WallbitServerError("boom", status=502)
        if _FakeClient.mode == "slow":
            time.sleep(0.2)
        if path == "/balance/stocks":
            return SimpleNamespace(data=_STOCKS)
        if path == "/balance/checking":
            return SimpleNamespace(data=_CHECKING)
        if path.startswith("/assets/"):
            return SimpleNamespace(data=_ASSETS[path.rsplit("/", 1)[1]])
        raise AssertionError(f"unexpected path {path}")

    @classmethod
    def reset(cls, mode="ok"):
        cls.calls = []
        cls.init_kwargs = []
        cls.mode = mode


def _account(account_id: int):
    return SimpleNamespace(id=account_id, encrypted_api_key="enc")


# --- Tests ----------------------------------------------------------------------


def _test_snapshot():
    from django.core.cache.backends.locmem import LocMemCache

    from wallbit import portfolio
    from wallbit.portfolio import WallbitUnavailableError, get_live_snapshot

    shared = LocMemCache("wb-test", {})
    shared.clear()
    portfolio._cache = lambda: shared  # type: ignore[assignment]
    portfolio.WallbitClient = _FakeClient  # type: ignore[assignment]
    portfolio.decrypt_api_key = lambda token: "plain-key"  # type: ignore[assignment]
    # Keep the single-flight wait short so the tests stay fast.
    portfolio._SNAPSHOT_WAIT_SECONDS = 2.0

    acct = _account(1)

    print("\n[snapshot] first call fetches once: stocks + checking + one /assets per held symbol")
    _FakeClient.reset()
    snap = get_live_snapshot(acct)
    _expect(not snap.stale and snap.has_data, "fresh snapshot, not stale")
    _expect(_FakeClient.calls == ["/balance/stocks", "/balance/checking", "/assets/NVDA", "/assets/SPCX"],
            f"exactly 4 upstream calls in order (got {_FakeClient.calls})")
    _expect(set(snap.assets) == {"NVDA", "SPCX"}, "USD pseudo-position gets no /assets call")
    _expect(snap.assets["NVDA"]["price"] == "180.0", "asset payload unwrapped from {data: …}")
    _expect(len(snap.positions) == 3 and len(snap.checking) == 1, "positions/checking rows kept raw")
    _expect(_FakeClient.init_kwargs == [{"retry_rate_limited": False}],
            "request-path client is built with retry_rate_limited=False")

    print("\n[snapshot] second call within TTL hits the cache — zero upstream calls")
    _FakeClient.reset()
    again = get_live_snapshot(acct)
    _expect(_FakeClient.calls == [], "no upstream calls on cache hit")
    _expect(again.fetched_at == snap.fetched_at and not again.stale, "same fetched_at, still fresh")

    print("\n[snapshot] 429 after the live copy expires → last-good served stale, cooldown armed")
    shared.delete(portfolio._snap_key(1))  # simulate TTL expiry, keep last-good
    _FakeClient.reset("rate_limited")
    stale = get_live_snapshot(acct)
    _expect(_FakeClient.calls == ["/balance/stocks"], "fails fast: a single upstream call, no Retry-After sleeps")
    _expect(stale.stale and stale.reason == "rate_limited", "served stale with reason=rate_limited")
    _expect(stale.assets == snap.assets and stale.fetched_at == snap.fetched_at, "stale data == last-good")
    _expect(shared.get(portfolio._snap_cooldown_key(1)) is not None, "cooldown key set")
    _expect(shared.get(portfolio._snap_lock_key(1)) is None, "lock released after failure")

    print("\n[snapshot] during the cooldown Wallbit is not called at all")
    _FakeClient.reset("ok")
    cooled = get_live_snapshot(acct)
    _expect(_FakeClient.calls == [], "no upstream call while cooling down")
    _expect(cooled.stale and cooled.reason == "rate_limited", "still stale/rate_limited")

    print("\n[snapshot] cooldown over → fresh fetch resumes")
    shared.delete(portfolio._snap_cooldown_key(1))
    _FakeClient.reset("ok")
    fresh = get_live_snapshot(acct)
    _expect(not fresh.stale and len(_FakeClient.calls) == 4, "fetched again once the cooldown expired")

    print("\n[snapshot] upstream 5xx → last-good stale (reason=upstream_error), no cooldown")
    shared.delete(portfolio._snap_key(1))
    _FakeClient.reset("server_error")
    degraded = get_live_snapshot(acct)
    _expect(degraded.stale and degraded.reason == "upstream_error", "stale with reason=upstream_error")
    _expect(shared.get(portfolio._snap_cooldown_key(1)) is None, "no cooldown for a non-429 failure")

    print("\n[snapshot] nothing to serve (no last-good) → WallbitUnavailableError, never zeros")
    acct2 = _account(2)
    _FakeClient.reset("rate_limited")
    _expect_raises(lambda: get_live_snapshot(acct2), WallbitUnavailableError,
                   "raises when rate-limited with no last-good")
    _FakeClient.reset("ok")
    empty = get_live_snapshot(acct2, require_data=False)  # cooldown active for acct 2
    _expect(_FakeClient.calls == [] and empty.stale and not empty.has_data,
            "require_data=False returns the empty stale snapshot without calling upstream")

    print("\n[snapshot] single-flight: a concurrent caller waits for the in-flight fetch")
    acct3 = _account(3)
    _FakeClient.reset("slow")
    results: dict[str, object] = {}

    def _worker(name):
        results[name] = get_live_snapshot(acct3)

    t1 = threading.Thread(target=_worker, args=("a",))
    t2 = threading.Thread(target=_worker, args=("b",))
    t1.start()
    time.sleep(0.1)  # let "a" take the lock first
    t2.start()
    t1.join()
    t2.join()
    stocks_calls = [c for c in _FakeClient.calls if c == "/balance/stocks"]
    _expect(len(stocks_calls) == 1, f"only one upstream fetch for two concurrent callers (got {len(stocks_calls)})")
    a, b = results["a"], results["b"]
    _expect(getattr(a, "fetched_at", None) == getattr(b, "fetched_at", None) and not b.stale,
            "waiter received the same fresh snapshot")

    print("\n[snapshot] lock holder fails → waiter falls back to last-good instead of hanging")
    acct4 = _account(4)
    # Seed a last-good so the waiter has something to serve.
    shared.set(portfolio._snap_lastgood_key(4), fresh.to_cache(), 3600)
    shared.add(portfolio._snap_lock_key(4), "1", 20)  # someone else is fetching

    def _release_without_result():
        time.sleep(0.3)
        shared.delete(portfolio._snap_lock_key(4))

    threading.Thread(target=_release_without_result).start()
    _FakeClient.reset("ok")
    started = time.monotonic()
    waited = get_live_snapshot(acct4)
    _expect(_FakeClient.calls == [], "waiter never fetched on its own")
    _expect(waited.stale and waited.reason == "upstream_error", "waiter served last-good stale")
    _expect(time.monotonic() - started < 1.5, "waiter returned promptly once the lock vanished")

    print("\n[snapshot] invalidate_snapshot drops the live copy but keeps last-good")
    portfolio.invalidate_snapshot(1)
    _expect(shared.get(portfolio._snap_key(1)) is None, "live key gone")
    _expect(shared.get(portfolio._snap_lastgood_key(1)) is not None, "last-good kept")

    print("\n[summary helpers] _cash_balances reads rows, skips zero/unknown currency")
    cash = portfolio._cash_balances([
        {"currency": "USD", "balance": "40.00"},
        {"currency": "ARS", "balance": "0"},
        {"balance": "5"},
    ])
    _expect([(c.currency, str(c.amount)) for c in cash] == [("USD", "40.00")], "only positive USD row kept")


def _test_client_rate_limit_policy():
    from wallbit import client as client_mod
    from wallbit.client import WallbitClient, WallbitRateLimitError

    print("\n[client] retry_rate_limited=False → 429 raises at once with retry_after, one request")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "60"}, json={"message": "rate-limited"})

    c = WallbitClient("k", base_url="https://wallbit.test", retry_rate_limited=False)
    c._client = httpx.Client(base_url="https://wallbit.test", transport=httpx.MockTransport(handler))
    try:
        c.get("/balance/stocks")
    except WallbitRateLimitError as exc:
        _expect(exc.retry_after == 60.0, "retry_after parsed from Retry-After")
        _expect(exc.status == 429, "status 429 on the error")
    else:
        _expect(False, "should have raised WallbitRateLimitError")
    _expect(len(seen) == 1, f"exactly one request (got {len(seen)})")

    print("\n[client] default policy still retries a 429 up to max_attempts")
    seen.clear()
    slept: list[float] = []
    client_mod.time.sleep = lambda s: slept.append(s)  # type: ignore[assignment]
    c2 = WallbitClient("k", base_url="https://wallbit.test", max_attempts=3)
    c2._client = httpx.Client(base_url="https://wallbit.test", transport=httpx.MockTransport(handler))
    _expect_raises(lambda: c2.get("/balance/stocks"), WallbitRateLimitError, "raises after retries")
    _expect(len(seen) == 3, f"three attempts (got {len(seen)})")
    _expect(len(slept) == 2 and all(s <= client_mod.BACKOFF_CAP for s in slept), "slept between attempts, capped")


def main() -> int:
    _setup()
    _test_snapshot()
    _test_client_rate_limit_policy()
    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all snapshot smoke tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
