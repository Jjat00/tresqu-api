"""Portfolio aggregation service.

Combines Investment rows stored in our DB with live calls to Wallbit
(``/balance/checking``, ``/balance/stocks``, ``/assets/{symbol}``) to
produce the summary, holdings and timeline used by the dashboard.

Live Wallbit reads go through ONE per-account ``LiveSnapshot`` (see
``get_live_snapshot``) cached in the shared Redis cache, so summary, holdings,
the P&L anchor and the agent tools all share a single upstream fetch per
minute — across gunicorn workers — instead of each hitting Wallbit on its own.
Wallbit sits behind Cloudflare rate limiting: a dashboard load that fired
~12 requests in a burst (3 endpoints × checking/stocks/assets) tripped it
("You are being rate-limited by the website owner's configuration"), and the
client's Retry-After sleeps then kept the block alive for minutes. The snapshot
is single-flight (concurrent callers wait for the in-flight fetch), falls back
to the last-good copy marked ``stale`` when Wallbit fails, and after a 429
stays away from Wallbit for a cooldown so the window can actually clear.
"""
from __future__ import annotations

import bisect
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from users.models import User

from .agent_safety import AccountNotConnected, get_account_or_raise
from .client import WallbitClient, WallbitError, WallbitRateLimitError
from .crypto import decrypt_api_key
from .models import PENDING_TX_STATUSES, Investment, WallbitAccount

logger = logging.getLogger(__name__)

# Live snapshot caching. The live TTL matches the dashboard's refetch interval
# (60 s) so the UI sees fresh numbers once a minute at most. Last-good is kept
# long enough to ride out an upstream outage / rate-limit block.
_SNAPSHOT_TTL = 60
_SNAPSHOT_LASTGOOD_TTL = 24 * 3600
# Single-flight: the first caller takes the lock and fetches; concurrent callers
# poll the cache for the result instead of racing Wallbit. The lock TTL bounds a
# crashed holder; the wait bounds how long a request blocks on a slow upstream.
_SNAPSHOT_LOCK_TTL = 30
_SNAPSHOT_WAIT_SECONDS = 20.0
_SNAPSHOT_POLL_SECONDS = 0.25
# After a 429 we stop calling Wallbit for this long (or Retry-After, capped).
# Every request inside the blocked window would otherwise extend it.
_RATE_LIMIT_COOLDOWN_DEFAULT = 90
_RATE_LIMIT_COOLDOWN_MAX = 600

# Wallbit's /balance/stocks returns cash balances as pseudo-positions whose
# "symbol" is the currency code (e.g. USD). Those are NOT holdings — they are
# the checking balance, already reported via /balance/checking — so we skip
# them when building positions/P&L (otherwise the agent confuses cash with a
# stock or with the Robo Advisor).
_CASH_SYMBOLS = {
    "USD", "EUR", "GBP", "ARS", "BRL", "MXN", "COP",
    "CLP", "UYU", "PEN", "USDT", "USDC",
}


def _settled_q() -> Q:
    """Match only Investments whose underlying trade has settled.

    A row with no linked mirror (legacy data, or an optimistic row before the
    sync adopts it) has no upstream status → counted. A row linked to a mirror
    whose status is one of ``PENDING_TX_STATUSES`` is a not-yet-executed trade
    → excluded from invested capital / P&L until it settles. Built as an
    explicit OR with ``isnull`` so NULL-mirror rows survive the join cleanly.
    """
    return Q(wallbit_tx__isnull=True) | ~Q(wallbit_tx__status__in=PENDING_TX_STATUSES)


class WallbitUnavailableError(WallbitError):
    """Wallbit could not be reached AND there is no last-good snapshot to serve.

    Raised instead of returning zeros so callers (views, P&L anchor, agent
    tools) can say "no disponible" rather than report an empty portfolio.
    """


@dataclass
class LiveSnapshot:
    """One account's live Wallbit state: cash rows, positions and asset details.

    ``stale`` is True when this is a last-good copy served because the fresh
    fetch failed; ``reason`` says why (``rate_limited``, ``upstream_error``,
    ``in_flight_timeout``). ``fetched_at`` is when the data was actually read
    from Wallbit (None only for the empty, nothing-to-serve snapshot).
    """

    checking: list[dict[str, Any]] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched_at: datetime | None = None
    stale: bool = False
    reason: str = ""

    @property
    def has_data(self) -> bool:
        return self.fetched_at is not None

    def to_cache(self) -> dict[str, Any]:
        # Plain dict (not the dataclass) so a code change never breaks unpickling
        # of an entry written by the previous deploy.
        return {
            "checking": self.checking,
            "positions": self.positions,
            "assets": self.assets,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }

    @classmethod
    def from_cache(cls, raw: Any, *, stale: bool = False, reason: str = "") -> "LiveSnapshot | None":
        if not isinstance(raw, dict):
            return None
        fetched_at = None
        if raw.get("fetched_at"):
            try:
                fetched_at = datetime.fromisoformat(raw["fetched_at"])
            except (TypeError, ValueError):
                fetched_at = None
        return cls(
            checking=list(raw.get("checking") or []),
            positions=list(raw.get("positions") or []),
            assets=dict(raw.get("assets") or {}),
            fetched_at=fetched_at,
            stale=stale,
            reason=reason,
        )


def _cache():
    """Shared (cross-worker) cache: the Redis one used by marketdata, else default."""
    try:
        return caches["marketdata"]
    except InvalidCacheBackendError:
        return caches["default"]


def _cache_get(key: str) -> Any:
    try:
        return _cache().get(key)
    except Exception as exc:  # noqa: BLE001 — cache must never break the request
        logger.warning("wallbit snapshot cache get failed (%s); treating as miss", exc)
        return None


def _cache_set(key: str, value: Any, ttl: int) -> None:
    try:
        _cache().set(key, value, ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wallbit snapshot cache set failed (%s); skipping", exc)


def _cache_add(key: str, value: Any, ttl: int) -> bool:
    """Atomic set-if-absent. On a broken cache backend we *pretend* we got the
    lock so the request still fetches (uncoordinated beats blocked)."""
    try:
        return bool(_cache().add(key, value, ttl))
    except Exception as exc:  # noqa: BLE001
        logger.warning("wallbit snapshot cache add failed (%s); fetching uncoordinated", exc)
        return True


def _cache_delete(key: str) -> None:
    try:
        _cache().delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wallbit snapshot cache delete failed (%s)", exc)


def _snap_key(account_id: int) -> str:
    return f"wb:snap:{account_id}"


def _snap_lastgood_key(account_id: int) -> str:
    return f"wb:snap:lastgood:{account_id}"


def _snap_lock_key(account_id: int) -> str:
    return f"wb:snap:lock:{account_id}"


def _snap_cooldown_key(account_id: int) -> str:
    return f"wb:snap:cooldown:{account_id}"


def invalidate_snapshot(account_id: int) -> None:
    """Drop the live copy (keeps last-good). Call after (re)connecting a key."""
    _cache_delete(_snap_key(account_id))


def _normalize_asset(payload: Any) -> dict[str, Any]:
    asset = payload.get("data", payload) if isinstance(payload, dict) else payload
    return asset if isinstance(asset, dict) else {}


def _fetch_snapshot(api_key: str) -> LiveSnapshot:
    """Read checking + stocks + one /assets call per held symbol from Wallbit.

    Fails fast on the first 429 (no Retry-After sleeps on the request path —
    see ``WallbitClient(retry_rate_limited=False)``) so the caller can fall
    back to last-good and start a cooldown. ``/balance/stocks`` failing sinks
    the fetch (positions are the point); a failing ``/balance/checking`` or
    ``/assets/{symbol}`` degrades that piece only, as before.
    """
    with WallbitClient(api_key, retry_rate_limited=False) as client:
        positions = _unwrap_list(client.get("/balance/stocks").data)

        try:
            checking = _unwrap_list(client.get("/balance/checking").data)
        except WallbitRateLimitError:
            raise
        except WallbitError as exc:
            logger.warning("portfolio: /balance/checking failed: %s", exc)
            checking = []

        assets: dict[str, dict[str, Any]] = {}
        for pos in positions:
            symbol = (pos.get("symbol") or "").upper()
            shares = _to_decimal(pos.get("shares") or pos.get("quantity"))
            if not symbol or symbol in _CASH_SYMBOLS or shares <= 0 or symbol in assets:
                continue
            try:
                assets[symbol] = _normalize_asset(client.get(f"/assets/{symbol}").data)
            except WallbitRateLimitError:
                raise
            except WallbitError as exc:
                logger.warning("portfolio: failed to fetch asset %s: %s", symbol, exc)
                assets[symbol] = {}

    return LiveSnapshot(
        checking=checking,
        positions=positions,
        assets=assets,
        fetched_at=timezone.now(),
    )


def _fallback_snapshot(account_id: int, reason: str) -> LiveSnapshot:
    last_good = LiveSnapshot.from_cache(
        _cache_get(_snap_lastgood_key(account_id)), stale=True, reason=reason
    )
    if last_good is not None and last_good.has_data:
        return last_good
    return LiveSnapshot(stale=True, reason=reason)


def get_live_snapshot(account: WallbitAccount, *, require_data: bool = True) -> LiveSnapshot:
    """Return the account's live snapshot: cached, freshly fetched, or last-good.

    Order: live cache hit → (cooldown active → last-good) → take the
    single-flight lock and fetch → otherwise wait for the in-flight fetch.
    With ``require_data`` (default) an empty fallback raises
    ``WallbitUnavailableError`` instead of returning zeros.
    """
    account_id = account.id
    live_key = _snap_key(account_id)

    snap = LiveSnapshot.from_cache(_cache_get(live_key))
    if snap is not None and snap.has_data:
        return snap

    def _finish(result: LiveSnapshot) -> LiveSnapshot:
        if require_data and not result.has_data:
            raise WallbitUnavailableError(
                f"Wallbit no disponible ({result.reason or 'sin datos'})",
                status=503,
            )
        return result

    cooldown_key = _snap_cooldown_key(account_id)
    if _cache_get(cooldown_key):
        return _finish(_fallback_snapshot(account_id, "rate_limited"))

    lock_key = _snap_lock_key(account_id)
    if _cache_add(lock_key, "1", _SNAPSHOT_LOCK_TTL):
        try:
            fresh = _fetch_snapshot(decrypt_api_key(account.encrypted_api_key))
            raw = fresh.to_cache()
            _cache_set(live_key, raw, _SNAPSHOT_TTL)
            _cache_set(_snap_lastgood_key(account_id), raw, _SNAPSHOT_LASTGOOD_TTL)
            return fresh
        except WallbitRateLimitError as exc:
            cooldown = int(min(exc.retry_after or _RATE_LIMIT_COOLDOWN_DEFAULT, _RATE_LIMIT_COOLDOWN_MAX))
            cooldown = max(cooldown, 1)
            _cache_set(cooldown_key, "1", cooldown)
            logger.warning(
                "portfolio: Wallbit rate-limited account %s; cooling down %ss and serving last-good",
                account_id,
                cooldown,
            )
            return _finish(_fallback_snapshot(account_id, "rate_limited"))
        except WallbitError as exc:
            logger.warning("portfolio: live snapshot fetch failed for account %s: %s", account_id, exc)
            return _finish(_fallback_snapshot(account_id, "upstream_error"))
        finally:
            _cache_delete(lock_key)

    # Another request is fetching right now — wait for its result.
    deadline = time.monotonic() + _SNAPSHOT_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(_SNAPSHOT_POLL_SECONDS)
        snap = LiveSnapshot.from_cache(_cache_get(live_key))
        if snap is not None and snap.has_data:
            return snap
        if _cache_get(cooldown_key):
            return _finish(_fallback_snapshot(account_id, "rate_limited"))
        if not _cache_get(lock_key):
            # Holder finished without a live entry → it failed; use last-good.
            return _finish(_fallback_snapshot(account_id, "upstream_error"))
    return _finish(_fallback_snapshot(account_id, "in_flight_timeout"))


def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(0)


def _unwrap_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    return []


@dataclass
class Holding:
    symbol: str
    shares: Decimal
    avg_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    cost_basis: Decimal
    pnl_usd: Decimal
    pnl_pct: float
    kind: str = ""
    name: str = ""
    # True when we hold the shares (live balance) but have no settled cost yet
    # (e.g. bought in the Wallbit app, not synced/settled). P&L is forced to 0
    # so the UI can show "sincronizando / —" instead of a phantom full-value gain.
    cost_pending: bool = False


@dataclass
class CashBalance:
    currency: str
    amount: Decimal


@dataclass
class PortfolioSummary:
    total_invested_usd: Decimal
    total_withdrawn_usd: Decimal
    net_invested_usd: Decimal
    current_value_usd: Decimal
    pnl_usd: Decimal
    pnl_pct: float
    holdings_count: int
    cash: list[CashBalance] = field(default_factory=list)
    robo_net_contributed_usd: Decimal = Decimal(0)
    # Uninvested USD sitting inside the investment account (Wallbit returns it
    # as a USD pseudo-position in /balance/stocks) — "libre para invertir".
    # Distinct from ``cash`` (/balance/checking), the main/non-investment account.
    investment_cash_usd: Decimal = Decimal(0)
    last_sync_at: datetime | None = None
    # Trades placed but not yet settled upstream (Wallbit status PENDING…).
    # Surfaced separately so the user sees the purchase without it counting as
    # invested capital or P&L. Each item: symbol, action, amount_usd, shares,
    # executed_at, status.
    pending_trades: list[dict[str, Any]] = field(default_factory=list)
    # True when the live numbers come from the last-good snapshot because
    # Wallbit failed / rate-limited us; ``as_of`` is when they were read.
    stale: bool = False
    as_of: datetime | None = None


@dataclass
class TimelinePoint:
    date: date
    invested_total_usd: Decimal


@dataclass
class PnLTimelinePoint:
    """A point on the gains/losses-over-time curve.

    ``pnl_usd`` = reconstructed market value − net invested at that date. The
    line crosses zero (above = gain, below = loss). ``invested_total_usd`` and
    ``market_value_usd`` are carried for the tooltip/context.

    ``date`` is a ``date`` for daily periods and a full ISO datetime string for
    the intraday "1d" period — serialized as a plain string either way.
    """
    date: Any
    pnl_usd: Decimal
    pnl_pct: float
    market_value_usd: Decimal
    invested_total_usd: Decimal


def _cost_basis_for_symbol(user: User, symbol: str) -> tuple[Decimal, Decimal]:
    """Return (net_shares, net_cost) for a STOCK/ETF/BOND symbol.

    BUY contributes +shares / +amount_usd; SELL contributes -shares / -amount_usd.
    Average cost = net_cost / net_shares (when shares > 0).
    """
    qs = Investment.objects.filter(user=user, symbol__iexact=symbol).filter(
        _settled_q()
    ).only("action", "amount_usd", "shares")
    net_shares = Decimal(0)
    net_cost = Decimal(0)
    for inv in qs:
        sign = 1 if inv.action == Investment.BUY else -1
        if inv.shares is not None:
            net_shares += Decimal(sign) * inv.shares
        net_cost += Decimal(sign) * inv.amount_usd
    return net_shares, net_cost


def get_holdings(user: User) -> list[Holding]:
    """Live snapshot of the user's positions with cost basis + P&L."""
    account = get_account_or_raise(user)
    snap = get_live_snapshot(account)

    holdings: list[Holding] = []
    for pos in snap.positions:
        symbol = (pos.get("symbol") or "").upper()
        shares = _to_decimal(pos.get("shares") or pos.get("quantity"))
        if not symbol or symbol in _CASH_SYMBOLS or shares <= 0:
            continue

        asset = snap.assets.get(symbol) or {}
        current_price = _to_decimal(asset.get("price") or asset.get("current_price"))
        kind = (asset.get("type") or asset.get("kind") or "").upper()
        name = asset.get("name") or asset.get("description") or ""

        net_shares, net_cost = _cost_basis_for_symbol(user, symbol)
        # Prefer Wallbit's reported shares; fall back to what we computed
        effective_shares = shares if shares > 0 else max(net_shares, Decimal(0))
        market_value = current_price * effective_shares
        if net_cost > 0:
            # Cost basis = the real USD put into the position (net_cost). We
            # use it directly instead of avg_cost * shares: amount_usd is
            # recorded exactly, while the stored share counts may have been
            # rounded, and multiplying an inflated avg_cost by the precise
            # Wallbit share count would amplify that into a wrong P&L.
            cost_basis = net_cost
            avg_cost = cost_basis / effective_shares if effective_shares > 0 else Decimal(0)
            pnl_usd = market_value - cost_basis
            pnl_pct = float(pnl_usd / cost_basis * 100) if cost_basis > 0 else 0.0
            cost_pending = False
        else:
            # We hold shares (live balance) but have NO settled cost for them
            # yet — typically a buy made in the Wallbit app that hasn't synced
            # or settled. Reporting cost 0 would invent a gain equal to the
            # whole position, so treat it as break-even and flag it so the UI
            # shows "sincronizando / —" until the real cost lands.
            cost_basis = market_value
            avg_cost = Decimal(0)
            pnl_usd = Decimal(0)
            pnl_pct = 0.0
            cost_pending = True

        holdings.append(
            Holding(
                symbol=symbol,
                shares=effective_shares,
                avg_cost=avg_cost,
                current_price=current_price,
                market_value=market_value,
                cost_basis=cost_basis,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                kind=kind,
                name=name,
                cost_pending=cost_pending,
            )
        )

    holdings.sort(key=lambda h: h.market_value, reverse=True)
    return holdings


def _cash_balances(rows: list[dict[str, Any]]) -> list[CashBalance]:
    out: list[CashBalance] = []
    for row in rows:
        currency = (row.get("currency") or row.get("code") or "").upper()
        amount = _to_decimal(row.get("balance") or row.get("amount"))
        if currency and amount > 0:
            out.append(CashBalance(currency=currency, amount=amount))
    return out


def get_summary(user: User) -> PortfolioSummary:
    """Top-level portfolio numbers for the hero cards."""
    account = get_account_or_raise(user)
    snap = get_live_snapshot(account)

    # Capital invertido en ACCIONES/ETFs únicamente (BUY/SELL). Los depósitos
    # al Robo Advisor (DEPOSIT/WITHDRAW) NO se suman aquí: su valor actual no
    # se puede medir (Wallbit no lo expone) y mezclarlo inflaba el "invertido"
    # contra un "valor actual" que solo cuenta acciones, mostrando una pérdida
    # falsa. El Robo Advisor se reporta aparte (robo_net_contributed_usd).
    # Only SETTLED trades count toward invested/withdrawn — a PENDING buy hasn't
    # debited cash nor become a live holding, so counting it would inflate the
    # cost against a current value that excludes it (false loss).
    invested_total = (
        Investment.objects.filter(user=user, action=Investment.BUY)
        .filter(_settled_q())
        .aggregate(s=Sum("amount_usd"))["s"]
        or Decimal(0)
    )
    withdrawn_total = (
        Investment.objects.filter(user=user, action=Investment.SELL)
        .filter(_settled_q())
        .aggregate(s=Sum("amount_usd"))["s"]
        or Decimal(0)
    )
    pending_trades = _pending_trades(user)

    current_value = Decimal(0)
    # Market value of held symbols with NO settled cost yet (bought in the
    # Wallbit app, not synced/settled). Folded into invested below so they net
    # to zero P&L (break-even) instead of inflating the gain by their full value.
    untracked_cost = Decimal(0)
    cash: list[CashBalance] = []
    holdings_count = 0
    investment_cash = Decimal(0)

    cash = _cash_balances(snap.checking)

    for pos in snap.positions:
        symbol = (pos.get("symbol") or "").upper()
        shares = _to_decimal(pos.get("shares") or pos.get("quantity"))
        if not symbol or shares <= 0:
            continue
        if symbol in _CASH_SYMBOLS:
            # Uninvested cash inside the investment account: for USD the
            # "shares" value IS the dollar amount free to invest.
            if symbol == "USD":
                investment_cash += shares
            continue
        asset = snap.assets.get(symbol) or {}
        price = _to_decimal(asset.get("price") or asset.get("current_price"))
        market_value = price * shares
        current_value += market_value
        holdings_count += 1
        _, net_cost = _cost_basis_for_symbol(user, symbol)
        if net_cost <= 0:
            untracked_cost += market_value

    # Fold the break-even placeholder for untracked holdings into invested so the
    # three hero cards stay coherent (net = bruto − retirado, pnl = value − net)
    # and no phantom gain leaks in while a real cost is still syncing.
    invested_total = Decimal(invested_total) + untracked_cost
    net_invested = invested_total - Decimal(withdrawn_total)

    pnl = current_value - net_invested
    pnl_pct = (
        float(pnl / net_invested * 100) if net_invested > 0 else 0.0
    )

    robo = get_robo_advisor_position(user)

    return PortfolioSummary(
        total_invested_usd=Decimal(invested_total),
        total_withdrawn_usd=Decimal(withdrawn_total),
        net_invested_usd=net_invested,
        current_value_usd=current_value,
        pnl_usd=pnl,
        pnl_pct=pnl_pct,
        holdings_count=holdings_count,
        cash=cash,
        robo_net_contributed_usd=robo["net_contributed_usd"],
        investment_cash_usd=investment_cash,
        last_sync_at=account.last_sync_at,
        pending_trades=pending_trades,
        stale=snap.stale,
        as_of=snap.fetched_at,
    )


def _pending_trades(user: User) -> list[dict[str, Any]]:
    """Not-yet-settled trades (Wallbit status PENDING…), newest first.

    Read from the mirrored Investment rows linked to a pending tx. Kept out of
    invested/P&L; the dashboard shows them in a separate "Pendientes" section.
    """
    qs = (
        Investment.objects.filter(
            user=user,
            action__in=[Investment.BUY, Investment.SELL],
            wallbit_tx__status__in=PENDING_TX_STATUSES,
        )
        .select_related("wallbit_tx")
        .order_by("-wallbit_tx__created_at_wallbit")
    )
    out: list[dict[str, Any]] = []
    for inv in qs:
        out.append({
            "symbol": inv.symbol,
            "action": inv.action,
            "amount_usd": inv.amount_usd,
            "shares": inv.shares,
            "executed_at": inv.executed_at or (inv.wallbit_tx.created_at_wallbit if inv.wallbit_tx else None),
            "status": (inv.wallbit_tx.status if inv.wallbit_tx else "PENDING"),
        })
    return out


_PERIOD_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "all": 3650}


def get_timeline(user: User, period: str = "3m") -> list[TimelinePoint]:
    """Daily series of cumulative net invested capital in stocks/ETFs.

    Counts ONLY BUY/SELL — Robo Advisor deposits/withdrawals are excluded so
    this curve matches the "Capital invertido" hero KPI (``net_invested_usd``,
    also BUY/SELL only). Mixing the robo in would inflate the line against a
    hero card that doesn't, showing two different "capital invertido" numbers.

    Groups by the real trade date (``executed_at``, falling back to
    ``created_at`` for rows that pre-date the field). One cumulative point
    per day with activity; the frontend renders the line with visual gaps.
    """
    days = _PERIOD_DAYS.get(period, 90)
    since = timezone.now() - timedelta(days=days)

    qs = (
        Investment.objects.filter(user=user, action__in=[Investment.BUY, Investment.SELL])
        .filter(_settled_q())
        .annotate(effective_at=Coalesce("executed_at", "created_at"))
        .filter(effective_at__gte=since)
        .order_by("effective_at")
        .values_list("effective_at", "action", "amount_usd")
    )

    daily_delta: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    for effective_at, action, amount in qs:
        sign = 1 if action == Investment.BUY else -1
        daily_delta[effective_at.date()] += Decimal(sign) * Decimal(amount)

    # Seed cumulative with historical sum before `since` so the series starts
    # at the user's real net-invested position, not zero.
    seed = (
        Investment.objects.filter(user=user)
        .filter(_settled_q())
        .annotate(effective_at=Coalesce("executed_at", "created_at"))
        .filter(effective_at__lt=since)
        .aggregate(
            buys=Sum("amount_usd", filter=_action_filter(Investment.BUY)),
            sells=Sum("amount_usd", filter=_action_filter(Investment.SELL)),
        )
    )
    cumulative = Decimal(seed.get("buys") or 0) - Decimal(seed.get("sells") or 0)

    points: list[TimelinePoint] = []
    for day in sorted(daily_delta.keys()):
        cumulative += daily_delta[day]
        points.append(TimelinePoint(date=day, invested_total_usd=cumulative))
    return points


def _action_filter(*actions: str):
    from django.db.models import Q
    q = Q()
    for action in actions:
        q |= Q(action=action)
    return q


# Left edge of the chart for each period, never older than the first investment
# (the user had no portfolio before that — nothing to plot).
def _pnl_window_start(period: str, first_date: date, today: date) -> date:
    p = (period or "1m").lower()
    if p == "1w":
        s = today - timedelta(days=7)
    elif p == "1m":
        s = today - timedelta(days=30)
    elif p == "1y":
        s = today - timedelta(days=365)
    elif p == "ytd":
        s = date(today.year, 1, 1)
    elif p == "all":
        s = first_date
    else:
        s = today - timedelta(days=30)
    return max(s, first_date)


# The market-data range is chosen from the FULL history span (first trade →
# today), NOT the requested window, so we always have prices at every trade
# date — needed to derive accurate share counts (see get_pnl_timeline). The
# window only decides which sample dates we emit.
def _md_range_for_span(first_date: date, today: date) -> str:
    span = (today - first_date).days
    if span <= 360:
        return "1y"  # daily closes
    if span <= 5 * 365:
        return "5y"  # weekly closes
    return "max"  # monthly closes


def _price_series(symbol: str, md_range: str) -> tuple[list[date], list[Decimal], bool] | None:
    """Return (sorted_dates, prices, stale) for ``symbol`` or None if unavailable.

    Collapses the market-data points to one close per calendar day. ``stale`` is
    True when the provider failed and a cached last-good payload was served.
    """
    from marketdata.service import get_price_history

    try:
        payload = get_price_history(symbol, md_range)
    except Exception as exc:  # ValueError (bad range) or MarketDataError
        logger.warning("pnl timeline: price history failed for %s (%s): %s", symbol, md_range, exc)
        return None

    by_date: dict[date, Decimal] = {}
    for p in payload.get("points") or []:
        t = p.get("t")
        price = p.get("price")
        if not t or price is None:
            continue
        try:
            d = datetime.strptime(str(t)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        by_date[d] = _to_decimal(price)  # points are ascending → last wins per day
    if not by_date:
        return None
    dates = sorted(by_date)
    return dates, [by_date[d] for d in dates], bool(payload.get("stale"))


def _price_on_or_before(series, d) -> Decimal | None:
    """Most recent price at/before ``d``. Works for daily (``date``) and
    intraday (``datetime``) series — bisect only needs the keys comparable to d.
    """
    if series is None:
        return None
    keys, prices, _ = series
    idx = bisect.bisect_right(keys, d) - 1
    if idx < 0:
        return None
    return prices[idx]


# Wallbit trades US stocks/ETFs → intraday timestamps from Twelve Data come in
# US Eastern (exchange) time as naive strings; localize them so they compare
# correctly with the UTC-aware ``executed_at``.
_EXCHANGE_TZ_NAME = "America/New_York"


def _parse_intraday_dt(value: Any, tz) -> datetime | None:
    s = str(value).strip().replace("Z", "").replace("T", " ")
    try:
        dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None  # daily "YYYY-MM-DD" has no time → not an intraday point
    return dt.replace(tzinfo=tz)


def _intraday_series(symbol: str, tz) -> tuple[list[datetime], list[Decimal], bool] | None:
    """Today's intraday (5-min) closes for ``symbol`` as tz-aware datetimes."""
    from marketdata.service import get_price_history

    try:
        payload = get_price_history(symbol, "1d")
    except Exception as exc:
        logger.warning("pnl intraday: price history failed for %s: %s", symbol, exc)
        return None
    by_dt: dict[datetime, Decimal] = {}
    for p in payload.get("points") or []:
        t = p.get("t")
        price = p.get("price")
        if not t or price is None:
            continue
        dt = _parse_intraday_dt(t, tz)
        if dt is not None:
            by_dt[dt] = _to_decimal(price)
    if not by_dt:
        return None
    dts = sorted(by_dt)
    return dts, [by_dt[d] for d in dts], bool(payload.get("stale"))


def _pnl_intraday(user: User) -> tuple[list[PnLTimelinePoint], bool]:
    """P&L through TODAY's intraday prices (the "1D" view).

    Values the holdings at each 5-min mark of the latest trading day. Trades are
    applied at their real ``executed_at`` time, so a brand-new user who bought
    today sees a flat 0 until the purchase and the live curve afterwards — no
    phantom gain/loss before they owned anything. Last point anchored to the
    live summary. Returns points whose ``date`` is a full ISO timestamp.
    """
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(_EXCHANGE_TZ_NAME)
    except Exception:
        from datetime import timezone as _tz
        tz = _tz.utc

    txns = list(
        Investment.objects.filter(user=user, action__in=[Investment.BUY, Investment.SELL])
        .filter(_settled_q())
        .annotate(effective_at=Coalesce("executed_at", "created_at"))
        .order_by("effective_at")
        .values_list("effective_at", "symbol", "action", "shares", "amount_usd")
    )
    if not txns:
        return [], False

    first_date = txns[0][0].date()
    today = timezone.now().date()
    daily_range = _md_range_for_span(first_date, today)
    symbols = {(s or "").upper() for _, s, _, _, _ in txns if s}

    daily = {sym: _price_series(sym, daily_range) for sym in symbols}
    intraday = {sym: _intraday_series(sym, tz) for sym in symbols}
    stale = any(intraday[s] is None or intraday[s][2] for s in symbols)

    all_dts = [dt for s in intraday.values() if s for dt in s[0]]

    def _live_anchor(ts: str) -> list[PnLTimelinePoint]:
        try:
            summary = get_summary(user)
            return [PnLTimelinePoint(
                date=ts,
                pnl_usd=summary.pnl_usd,
                pnl_pct=summary.pnl_pct,
                market_value_usd=summary.current_value_usd,
                invested_total_usd=summary.net_invested_usd,
            )]
        except Exception as exc:
            logger.warning("pnl intraday: live anchor failed for user %s: %s", user.id, exc)
            return []

    if not all_dts:
        # No intraday data (e.g. provider down) → just the live point.
        return _live_anchor(timezone.now().isoformat()), True

    trading_day = max(all_dts).date()  # in exchange tz

    # Build start-of-day holdings (trades before the trading day) and the list of
    # trades that happened DURING the trading day (applied at their timestamp).
    start_shares: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    start_cost: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    start_ninv = Decimal(0)
    today_trades: list[tuple[datetime, str, Decimal, Decimal]] = []
    for effective_at, symbol, action, shares, amount in txns:
        sym = (symbol or "").upper()
        if not sym:
            continue
        sign = 1 if action == Investment.BUY else -1
        amt = _to_decimal(amount)
        et_date = effective_at.astimezone(tz).date()
        if et_date < trading_day:
            price = _price_on_or_before(daily.get(sym), effective_at.date())
        elif et_date == trading_day:
            price = _price_on_or_before(intraday.get(sym), effective_at) or _price_on_or_before(
                daily.get(sym), effective_at.date()
            )
        else:
            continue  # future-dated, ignore
        if price is not None and price > 0:
            derived = amt / price
        else:
            derived = shares if shares is not None else Decimal(0)
            stale = True
        if et_date < trading_day:
            start_shares[sym] += Decimal(sign) * derived
            start_cost[sym] += Decimal(sign) * amt
            start_ninv += Decimal(sign) * amt
        else:
            today_trades.append((effective_at, sym, Decimal(sign) * derived, Decimal(sign) * amt))
    today_trades.sort(key=lambda x: x[0])

    sample_dts = sorted({dt for s in intraday.values() if s for dt in s[0] if dt.date() == trading_day})

    cur_shares = dict(start_shares)
    cur_cost = dict(start_cost)
    ninv = start_ninv
    ti = 0
    points: list[PnLTimelinePoint] = []
    for dt in sample_dts:
        while ti < len(today_trades) and today_trades[ti][0] <= dt:
            _, sym, dsh, damt = today_trades[ti]
            cur_shares[sym] = cur_shares.get(sym, Decimal(0)) + dsh
            cur_cost[sym] = cur_cost.get(sym, Decimal(0)) + damt
            ninv += damt
            ti += 1
        market_value = Decimal(0)
        for sym, sh in cur_shares.items():
            if sh > Decimal("0.00000001"):
                price = _price_on_or_before(intraday.get(sym), dt)
                market_value += (price * sh) if price is not None else max(cur_cost.get(sym, Decimal(0)), Decimal(0))
        pnl = market_value - ninv
        pnl_pct = float(pnl / ninv * 100) if ninv > 0 else 0.0
        points.append(PnLTimelinePoint(
            date=dt.isoformat(),
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            market_value_usd=market_value,
            invested_total_usd=ninv,
        ))

    # Anchor the last point to the live summary (keep its timestamp so the
    # intraday x-axis stays tidy even on weekends/after close).
    anchor = _live_anchor(points[-1].date if points else timezone.now().isoformat())
    if anchor:
        if points:
            points[-1] = anchor[0]
        else:
            points = anchor
    return points, stale


def get_pnl_timeline(user: User, period: str = "1m") -> tuple[list[PnLTimelinePoint], bool]:
    """Reconstruct the gains/losses (P&L) of the stock/ETF portfolio over time.

    There is no stored history of portfolio *value* — only the transaction
    ledger (``Investment``) and historical *prices* (``marketdata``). So for
    each sample date we rebuild the holdings as of that date and value them with
    the historical close on/just-before that date, then subtract the net
    invested at that date to get P&L.

    Shares are NOT taken from ``Investment.shares`` (stored truncated, which
    distorts the whole curve — e.g. a $20 META buy at 0.02 shares implies a
    $1000 price). Instead each trade's share count is derived from
    ``amount_usd / historical_price_on_the_trade_date``. This makes the value on
    the trade date equal the cost (P&L ≈ 0 that day) and keeps the series
    internally consistent. The LAST point is still anchored to the LIVE
    ``get_summary`` so the endpoint matches the hero cards exactly.

    The left edge is never older than the first investment. ``stale`` is set
    when any price (a trade-date or a symbol series) is missing, in which case
    that piece falls back to the truncated shares / cost. Robo Advisor / Chest
    movements are excluded (no live valuation), matching ``get_summary``.

    The "1d" period is intraday (today's price movement) and handled separately.
    """
    if (period or "").lower() == "1d":
        return _pnl_intraday(user)

    today = timezone.now().date()

    # Settled stock/ETF/BOND trades only (same scope as the invested timeline).
    txns = list(
        Investment.objects.filter(user=user, action__in=[Investment.BUY, Investment.SELL])
        .filter(_settled_q())
        .annotate(effective_at=Coalesce("executed_at", "created_at"))
        .order_by("effective_at")
        .values_list("effective_at", "symbol", "action", "shares", "amount_usd")
    )
    if not txns:
        return [], False

    first_date = txns[0][0].date()
    start_date = _pnl_window_start(period, first_date, today)
    # Range chosen from the full history so we have a price at every trade date.
    md_range = _md_range_for_span(first_date, today)

    # Normalize transactions and collect symbols.
    norm: list[tuple[date, str, int, Decimal | None, Decimal]] = []
    symbols: set[str] = set()
    for effective_at, symbol, action, shares, amount in txns:
        sym = (symbol or "").upper()
        if not sym:
            continue
        sign = 1 if action == Investment.BUY else -1
        norm.append((effective_at.date(), sym, sign, shares, _to_decimal(amount)))
        symbols.add(sym)

    # Fetch one historical price series per symbol (covers the full span).
    price_series: dict[str, tuple[list[date], list[Decimal], bool] | None] = {}
    stale = False
    for sym in symbols:
        series = _price_series(sym, md_range)
        price_series[sym] = series
        if series is None or series[2]:
            stale = True

    # Sample dates = union of price dates in [start_date, today]; ensure today.
    sample_set: set[date] = set()
    for series in price_series.values():
        if series is None:
            continue
        for d in series[0]:
            if start_date <= d <= today:
                sample_set.add(d)
    sample_set.add(today)
    sample_dates = sorted(sample_set)

    # Walk sample dates and the (sorted) ledger together, accumulating positions.
    # running_shares holds price-derived shares; running_cost holds exact USD.
    running_shares: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    running_cost: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    net_invested = Decimal(0)
    ti = 0
    points: list[PnLTimelinePoint] = []
    for d in sample_dates:
        while ti < len(norm) and norm[ti][0] <= d:
            tx_date, sym, sign, shares, amount = norm[ti]
            # Derive shares from the price on the trade date — NOT the stored
            # (truncated) share count. Fall back to stored shares if no price.
            tx_price = _price_on_or_before(price_series.get(sym), tx_date)
            if tx_price is not None and tx_price > 0:
                derived = amount / tx_price
            else:
                derived = shares if shares is not None else Decimal(0)
                stale = True
            running_shares[sym] += Decimal(sign) * derived
            running_cost[sym] += Decimal(sign) * amount
            net_invested += Decimal(sign) * amount
            ti += 1

        market_value = Decimal(0)
        for sym, sh in running_shares.items():
            cost = running_cost[sym]
            if sh > Decimal("0.00000001"):
                price = _price_on_or_before(price_series.get(sym), d)
                # No price for a held symbol → neutral (value = its cost).
                market_value += (price * sh) if price is not None else max(cost, Decimal(0))
            elif cost > 0:
                # Held but shares couldn't be derived → neutral contribution.
                market_value += cost

        pnl = market_value - net_invested
        pnl_pct = float(pnl / net_invested * 100) if net_invested > 0 else 0.0
        points.append(
            PnLTimelinePoint(
                date=d,
                pnl_usd=pnl,
                pnl_pct=pnl_pct,
                market_value_usd=market_value,
                invested_total_usd=net_invested,
            )
        )

    # Anchor the final point to the LIVE summary so the curve's endpoint matches
    # the hero cards exactly (avoids two contradicting "today" P&L numbers).
    try:
        summary = get_summary(user)
        anchor = PnLTimelinePoint(
            date=today,
            pnl_usd=summary.pnl_usd,
            pnl_pct=summary.pnl_pct,
            market_value_usd=summary.current_value_usd,
            invested_total_usd=summary.net_invested_usd,
        )
        if points and points[-1].date == today:
            points[-1] = anchor
        else:
            points.append(anchor)
    except Exception as exc:
        logger.warning("pnl timeline: live anchor failed for user %s: %s", user.id, exc)

    return points, stale


def get_robo_advisor_position(user: User) -> dict[str, Any]:
    """Net amount the user has put into Robo Advisors / Chests.

    Wallbit's public API exposes **no** endpoint for a robo advisor's current
    value (only ``POST /roboadvisor/deposit`` and ``/withdraw``), so we cannot
    know its live valuation or P&L. We only report the net contributed
    (deposits − withdrawals) from the Investment history we mirror locally.
    Callers must surface ``live_valuation_available=False`` and never present
    the net as a gain/loss.
    """
    deposited = (
        Investment.objects.filter(
            user=user,
            kind__in=[Investment.ROBO, Investment.CHEST],
            action=Investment.DEPOSIT,
        ).aggregate(s=Sum("amount_usd"))["s"]
        or Decimal(0)
    )
    withdrawn = (
        Investment.objects.filter(
            user=user,
            kind__in=[Investment.ROBO, Investment.CHEST],
            action=Investment.WITHDRAW,
        ).aggregate(s=Sum("amount_usd"))["s"]
        or Decimal(0)
    )
    deposited = Decimal(deposited)
    withdrawn = Decimal(withdrawn)
    return {
        "has_activity": bool(deposited or withdrawn),
        "net_contributed_usd": deposited - withdrawn,
        "deposited_usd": deposited,
        "withdrawn_usd": withdrawn,
        "live_valuation_available": False,
    }


def safe_get_summary(user: User) -> PortfolioSummary | None:
    try:
        return get_summary(user)
    except AccountNotConnected:
        return None


def safe_get_holdings(user: User) -> list[Holding]:
    try:
        return get_holdings(user)
    except AccountNotConnected:
        return []
