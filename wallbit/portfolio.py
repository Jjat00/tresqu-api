"""Portfolio aggregation service.

Combines Investment rows stored in our DB with live calls to Wallbit
(``/balance/checking``, ``/balance/stocks``, ``/assets/{symbol}``) to
produce the summary, holdings and timeline used by the dashboard.

Live Wallbit responses are cached in-process for 60 seconds so that
multiple endpoints called within the same dashboard load (summary +
holdings) only hit Wallbit once per symbol.
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

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from users.models import User

from .agent_safety import AccountNotConnected, get_account_or_raise
from .client import WallbitClient, WallbitError
from .crypto import decrypt_api_key
from .models import PENDING_TX_STATUSES, Investment, WallbitAccount

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60
_asset_cache: dict[str, tuple[float, dict[str, Any]]] = {}

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


def _cached_asset(client: WallbitClient, symbol: str) -> dict[str, Any] | None:
    now = time.time()
    cached = _asset_cache.get(symbol)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]
    try:
        response = client.get(f"/assets/{symbol}")
    except WallbitError as exc:
        logger.warning("portfolio: failed to fetch asset %s: %s", symbol, exc)
        return None
    payload = response.data or {}
    asset = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(asset, dict):
        return None
    _asset_cache[symbol] = (now, asset)
    return asset


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
    """
    date: date
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
    api_key = decrypt_api_key(account.encrypted_api_key)

    holdings: list[Holding] = []
    with WallbitClient(api_key) as client:
        try:
            stocks_response = client.get("/balance/stocks")
        except WallbitError as exc:
            logger.warning("portfolio: /balance/stocks failed for user %s: %s", user.id, exc)
            return []

        positions = _unwrap_list(stocks_response.data)
        for pos in positions:
            symbol = (pos.get("symbol") or "").upper()
            shares = _to_decimal(pos.get("shares") or pos.get("quantity"))
            if not symbol or symbol in _CASH_SYMBOLS or shares <= 0:
                continue

            asset = _cached_asset(client, symbol) or {}
            current_price = _to_decimal(asset.get("price") or asset.get("current_price"))
            kind = (asset.get("type") or asset.get("kind") or "").upper()
            name = asset.get("name") or asset.get("description") or ""

            net_shares, net_cost = _cost_basis_for_symbol(user, symbol)
            # Prefer Wallbit's reported shares; fall back to what we computed
            effective_shares = shares if shares > 0 else max(net_shares, Decimal(0))
            # Cost basis = the real USD put into the position (net_cost). We use
            # it directly instead of avg_cost * shares: amount_usd is recorded
            # exactly, while the stored share counts may have been rounded, and
            # multiplying an inflated avg_cost by the precise Wallbit share count
            # would amplify that rounding into a wrong P&L.
            cost_basis = net_cost if net_cost > 0 else Decimal(0)
            avg_cost = (
                cost_basis / effective_shares
                if effective_shares > 0 and cost_basis > 0
                else Decimal(0)
            )
            market_value = current_price * effective_shares
            pnl_usd = market_value - cost_basis
            pnl_pct = float(pnl_usd / cost_basis * 100) if cost_basis > 0 else 0.0

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
                )
            )

    holdings.sort(key=lambda h: h.market_value, reverse=True)
    return holdings


def _cash_balances(client: WallbitClient) -> list[CashBalance]:
    try:
        response = client.get("/balance/checking")
    except WallbitError as exc:
        logger.warning("portfolio: /balance/checking failed: %s", exc)
        return []
    rows = _unwrap_list(response.data)
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
    api_key = decrypt_api_key(account.encrypted_api_key)

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
    net_invested = Decimal(invested_total) - Decimal(withdrawn_total)

    pending_trades = _pending_trades(user)

    current_value = Decimal(0)
    cash: list[CashBalance] = []
    holdings_count = 0
    investment_cash = Decimal(0)

    with WallbitClient(api_key) as client:
        cash = _cash_balances(client)

        # Reuse the holdings function indirectly to keep symbol caching warm
        stocks_response = None
        try:
            stocks_response = client.get("/balance/stocks")
        except WallbitError as exc:
            logger.warning("portfolio summary: /balance/stocks failed: %s", exc)

        if stocks_response is not None:
            positions = _unwrap_list(stocks_response.data)
            for pos in positions:
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
                asset = _cached_asset(client, symbol) or {}
                price = _to_decimal(asset.get("price") or asset.get("current_price"))
                current_value += price * shares
                holdings_count += 1

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


# Maps the chart's period to (start date, market-data range to fetch). The
# market-data range only controls price granularity/density — the sample dates
# are derived from the prices actually returned, clipped to the start date.
def _pnl_window(period: str, first_date: date | None, today: date) -> tuple[date, str]:
    p = (period or "1m").lower()
    if p == "1w":
        return today - timedelta(days=7), "1m"
    if p == "1m":
        return today - timedelta(days=30), "1m"
    if p == "1y":
        return today - timedelta(days=365), "1y"
    if p == "ytd":
        return date(today.year, 1, 1), "1y"
    if p == "all":
        start = first_date or (today - timedelta(days=30))
        span = (today - start).days
        if span <= 330:
            md = "1y"
        elif span <= 5 * 365:
            md = "5y"
        else:
            md = "max"
        return start, md
    return today - timedelta(days=30), "1m"


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


def _price_on_or_before(series: tuple[list[date], list[Decimal], bool] | None, d: date) -> Decimal | None:
    if series is None:
        return None
    dates, prices, _ = series
    idx = bisect.bisect_right(dates, d) - 1
    if idx < 0:
        return None
    return prices[idx]


def get_pnl_timeline(user: User, period: str = "1m") -> tuple[list[PnLTimelinePoint], bool]:
    """Reconstruct the gains/losses (P&L) of the stock/ETF portfolio over time.

    There is no stored history of portfolio *value* — only the transaction
    ledger (``Investment``) and historical *prices* (``marketdata``). So for
    each sample date we rebuild the holdings as of that date (signed shares of
    settled BUY/SELL) and value them with the historical close on/just-before
    that date, then subtract the net invested at that date to get P&L.

    Caveats (returned via the ``stale`` flag so the UI can note approximation):
    - ``Investment.shares`` is stored truncated, so past points can be slightly
      off. To avoid contradicting the hero cards, the LAST point is anchored to
      the LIVE ``get_summary`` value/P&L.
    - If a symbol's price history is unavailable, that symbol falls back to its
      cost (neutral P&L contribution) and ``stale`` is set.
    - Robo Advisor / Chest movements are excluded (no live valuation), matching
      ``get_summary``'s invested/P&L scope.
    """
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
    since, md_range = _pnl_window(period, first_date, today)
    start_date = max(since, first_date)

    # Normalize transactions and collect symbols.
    norm: list[tuple[date, str, int, Decimal | None, Decimal]] = []
    symbols: set[str] = set()
    shares_unknown = False
    for effective_at, symbol, action, shares, amount in txns:
        sym = (symbol or "").upper()
        if not sym:
            continue
        sign = 1 if action == Investment.BUY else -1
        norm.append((effective_at.date(), sym, sign, shares, _to_decimal(amount)))
        symbols.add(sym)
        if shares is None:
            shares_unknown = True

    # Fetch one historical price series per symbol.
    price_series: dict[str, tuple[list[date], list[Decimal], bool] | None] = {}
    stale = shares_unknown
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
    running_shares: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    running_cost: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    net_invested = Decimal(0)
    ti = 0
    points: list[PnLTimelinePoint] = []
    for d in sample_dates:
        while ti < len(norm) and norm[ti][0] <= d:
            _, sym, sign, shares, amount = norm[ti]
            if shares is not None:
                running_shares[sym] += Decimal(sign) * shares
            running_cost[sym] += Decimal(sign) * amount
            net_invested += Decimal(sign) * amount
            ti += 1

        market_value = Decimal(0)
        for sym, sh in running_shares.items():
            cost = running_cost[sym]
            if sh > 0:
                price = _price_on_or_before(price_series.get(sym), d)
                # No price for a held symbol → neutral (value = its cost).
                market_value += (price * sh) if price is not None else max(cost, Decimal(0))
            elif cost > 0:
                # Held but shares unknown (legacy NULL) → neutral contribution.
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
