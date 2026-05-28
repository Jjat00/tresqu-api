"""Smoke tests for the market-analyst tools.

No network / DB: dependencies (portfolio, effective profile, marketdata
service) are monkeypatched and a stub user (with ``external_id``) is passed
so the tools skip the ORM lookup.

    python -m agents.tests.test_analyst_tools_smoke
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from decimal import Decimal

failures: list[str] = []


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _expect(cond: bool, label: str) -> None:
    print(f"    {'OK ' if cond else 'FAIL'} — {label}")
    if not cond:
        failures.append(label)


@dataclass
class _StubUser:
    external_id: str = "ext-123"
    id: int = 1


def _run() -> int:
    from agents.subagents import analyst_tools
    from wallbit import portfolio as portfolio_mod
    from wallbit.portfolio import CashBalance, Holding, PortfolioSummary

    user = _StubUser()
    tools = {t.name: t for t in analyst_tools.make_analyst_tools(user.external_id, user=user)}
    _expect(
        set(tools) == {"get_asset_quote", "get_price_history", "get_user_risk_profile", "get_user_portfolio"},
        "all four analyst tools present",
    )

    print("\n[get_price_history] normalizes range synonyms + returns compact summary")
    captured = {}

    def _fake_history(symbol, range_):
        captured["symbol"] = symbol
        captured["range"] = range_
        return {
            "symbol": symbol.upper(),
            "range": range_,
            "points": [{"t": "2026-05-01", "price": 1.0}],
            "summary": {"current": 1.0, "change_pct": 5.0, "trend": "alcista"},
            "stale": False,
        }

    import marketdata.service as md_service

    md_service.get_price_history = _fake_history  # type: ignore[assignment]
    out = tools["get_price_history"].invoke({"symbol": "nvda", "range": "month"})
    _expect(captured["range"] == "1m", "synonym 'month' normalized to '1m'")
    _expect(out["ok"] is True and "summary" in out and "points" not in out, "compact summary (no raw points)")

    print("\n[get_user_risk_profile] surfaces tolerance/source")

    @dataclass
    class _Eff:
        tolerance: str = "moderate"
        score: int = 55
        confidence: float = 0.7
        source: str = "inferred"
        warning: str | None = "completa /perfil"

    import agents.effective_profile as ep

    ep.get_effective_profile = lambda u, refresh_inference=False: _Eff()  # type: ignore[assignment]
    prof = tools["get_user_risk_profile"].invoke({})
    _expect(prof["tolerance"] == "moderate" and prof["source"] == "inferred", "tolerance + source surfaced")

    print("\n[get_user_portfolio] computes per-symbol weight")

    def _fake_summary(u):
        return PortfolioSummary(
            total_invested_usd=Decimal("800"), total_withdrawn_usd=Decimal("0"),
            net_invested_usd=Decimal("800"), current_value_usd=Decimal("1000"),
            pnl_usd=Decimal("200"), pnl_pct=25.0, holdings_count=2,
            cash=[CashBalance(currency="USD", amount=Decimal("0"))], last_sync_at=None,
        )

    def _fake_holdings(u):
        return [
            Holding(symbol="NVDA", shares=Decimal("1"), avg_cost=Decimal("600"),
                    current_price=Decimal("700"), market_value=Decimal("700"),
                    cost_basis=Decimal("600"), pnl_usd=Decimal("100"), pnl_pct=16.6, kind="STOCK", name="Nvidia"),
            Holding(symbol="VOO", shares=Decimal("1"), avg_cost=Decimal("300"),
                    current_price=Decimal("300"), market_value=Decimal("300"),
                    cost_basis=Decimal("300"), pnl_usd=Decimal("0"), pnl_pct=0.0, kind="ETF", name="Vanguard"),
        ]

    portfolio_mod.safe_get_summary = _fake_summary  # type: ignore[assignment]
    portfolio_mod.safe_get_holdings = _fake_holdings  # type: ignore[assignment]
    pf = tools["get_user_portfolio"].invoke({})
    _expect(pf["connected"] is True, "connected True")
    nvda = next(h for h in pf["holdings"] if h["symbol"] == "NVDA")
    _expect(nvda["weight_pct"] == 70.0, "NVDA weight = 700/1000 = 70%")

    print("\n[get_user_portfolio] no Wallbit → connected False")
    portfolio_mod.safe_get_summary = lambda u: None  # type: ignore[assignment]
    pf_none = tools["get_user_portfolio"].invoke({})
    _expect(pf_none["connected"] is False, "connected False when no account")

    if failures:
        print(f"\nANALYST TOOLS SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nANALYST TOOLS SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
