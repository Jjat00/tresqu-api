"""Portfolio aggregation service.

Combines Investment rows stored in our DB with live calls to Wallbit
(``/balance/checking``, ``/balance/stocks``, ``/assets/{symbol}``) to
produce the summary, holdings and timeline used by the dashboard.

Live Wallbit responses are cached in-process for 60 seconds so that
multiple endpoints called within the same dashboard load (summary +
holdings) only hit Wallbit once per symbol.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from users.models import User

from .agent_safety import AccountNotConnected, get_account_or_raise
from .client import WallbitClient, WallbitError
from .crypto import decrypt_api_key
from .models import Investment, WallbitAccount

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60
_asset_cache: dict[str, tuple[float, dict[str, Any]]] = {}


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
    last_sync_at: datetime | None = None


@dataclass
class TimelinePoint:
    date: date
    invested_total_usd: Decimal


def _cost_basis_for_symbol(user: User, symbol: str) -> tuple[Decimal, Decimal]:
    """Return (net_shares, net_cost) for a STOCK/ETF/BOND symbol.

    BUY contributes +shares / +amount_usd; SELL contributes -shares / -amount_usd.
    Average cost = net_cost / net_shares (when shares > 0).
    """
    qs = Investment.objects.filter(user=user, symbol__iexact=symbol).only(
        "action", "amount_usd", "shares"
    )
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
            if not symbol or shares <= 0:
                continue

            asset = _cached_asset(client, symbol) or {}
            current_price = _to_decimal(asset.get("price") or asset.get("current_price"))
            kind = (asset.get("type") or asset.get("kind") or "").upper()
            name = asset.get("name") or asset.get("description") or ""

            net_shares, net_cost = _cost_basis_for_symbol(user, symbol)
            # Prefer Wallbit's reported shares; fall back to what we computed
            effective_shares = shares if shares > 0 else max(net_shares, Decimal(0))
            avg_cost = (
                net_cost / net_shares
                if net_shares > 0
                else Decimal(0)
            )
            cost_basis = avg_cost * effective_shares
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

    invested_total = (
        Investment.objects.filter(
            user=user, action__in=[Investment.BUY, Investment.DEPOSIT]
        ).aggregate(s=Sum("amount_usd"))["s"]
        or Decimal(0)
    )
    withdrawn_total = (
        Investment.objects.filter(
            user=user, action__in=[Investment.SELL, Investment.WITHDRAW]
        ).aggregate(s=Sum("amount_usd"))["s"]
        or Decimal(0)
    )
    net_invested = Decimal(invested_total) - Decimal(withdrawn_total)

    current_value = Decimal(0)
    cash: list[CashBalance] = []
    holdings_count = 0

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
                asset = _cached_asset(client, symbol) or {}
                price = _to_decimal(asset.get("price") or asset.get("current_price"))
                current_value += price * shares
                holdings_count += 1

    pnl = current_value - net_invested
    pnl_pct = (
        float(pnl / net_invested * 100) if net_invested > 0 else 0.0
    )

    return PortfolioSummary(
        total_invested_usd=Decimal(invested_total),
        total_withdrawn_usd=Decimal(withdrawn_total),
        net_invested_usd=net_invested,
        current_value_usd=current_value,
        pnl_usd=pnl,
        pnl_pct=pnl_pct,
        holdings_count=holdings_count,
        cash=cash,
        last_sync_at=account.last_sync_at,
    )


_PERIOD_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "all": 3650}


def get_timeline(user: User, period: str = "3m") -> list[TimelinePoint]:
    """Daily series of cumulative net invested capital.

    Walks Investment rows in chronological order and emits one cumulative
    point per day where activity happened. The frontend fills gaps visually.
    """
    days = _PERIOD_DAYS.get(period, 90)
    since = timezone.now() - timedelta(days=days)

    qs = (
        Investment.objects.filter(user=user, created_at__gte=since)
        .order_by("created_at")
        .values_list("created_at", "action", "amount_usd")
    )

    daily_delta: dict[date, Decimal] = defaultdict(lambda: Decimal(0))
    for created_at, action, amount in qs:
        sign = 1 if action in (Investment.BUY, Investment.DEPOSIT) else -1
        daily_delta[created_at.date()] += Decimal(sign) * Decimal(amount)

    # Seed cumulative with historical sum before `since` so the series starts
    # at the user's real net-invested position, not zero.
    seed = (
        Investment.objects.filter(user=user, created_at__lt=since)
        .aggregate(
            buys=Sum("amount_usd", filter=_action_filter(Investment.BUY, Investment.DEPOSIT)),
            sells=Sum("amount_usd", filter=_action_filter(Investment.SELL, Investment.WITHDRAW)),
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
