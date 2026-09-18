"""Holdings/summary must not invent a gain when the live balance holds more
shares than the settled trades explain.

Regression for 2026-09-02: four SPCX fills Wallbit had executed were not in
the mirror yet, so 233.06 USD of known cost was spread over 2.06 live shares →
"costo prom. 113 USD, +57 USD" on a position that was actually down ~23 USD.
"""
from __future__ import annotations

from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from users.models import User
from wallbit.models import Investment, WallbitAccount
from wallbit.portfolio import (
    LiveSnapshot,
    _untracked_shares,
    get_holdings,
    get_summary,
)


def _snapshot(positions, assets) -> LiveSnapshot:
    return LiveSnapshot(
        checking=[], positions=positions, assets=assets, fetched_at=timezone.now()
    )


class UntrackedSharesTests(TestCase):
    def test_rounding_noise_is_ignored(self):
        # NVDA: 6 fills rounded to 2 dp by Wallbit → live 1.36416933 vs synced 1.33.
        self.assertEqual(
            _untracked_shares(Decimal("1.36416933"), Decimal("1.33"), 6), Decimal(0)
        )

    def test_unsynced_fills_are_detected(self):
        extra = _untracked_shares(Decimal("2.06224469"), Decimal("1.48"), 4)
        self.assertEqual(extra, Decimal("2.06224469") - Decimal("1.48"))


class PartialCostHoldingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            external_id="web-partial", platform="web", username="p", first_name="P"
        )
        WallbitAccount.objects.create(
            user=self.user, encrypted_api_key="enc", status=WallbitAccount.CONNECTED
        )
        now = timezone.now()
        for usd, shares in (("164.43", "1.00"), ("46.67", "0.29"), ("20.06", "0.18"), ("1.90", "0.01")):
            Investment.objects.create(
                user=self.user, kind=Investment.STOCK, action=Investment.BUY,
                symbol="SPCX", amount_usd=Decimal(usd), shares=Decimal(shares), executed_at=now,
            )
        self.live_shares = Decimal("2.06224469")
        self.price = Decimal("140.69")
        self.snapshot = _snapshot(
            positions=[{"symbol": "SPCX", "shares": str(self.live_shares)}],
            assets={"SPCX": {"price": str(self.price), "name": "Space Exploration", "type": "STOCK"}},
        )

    def test_holding_carries_untracked_shares_at_break_even(self):
        with mock.patch("wallbit.portfolio.get_live_snapshot", return_value=self.snapshot):
            holdings = get_holdings(self.user)

        self.assertEqual(len(holdings), 1)
        h = holdings[0]
        known_cost = Decimal("233.06")
        untracked = self.live_shares - Decimal("1.48")
        self.assertTrue(h.cost_pending)
        self.assertEqual(h.cost_basis, known_cost + untracked * self.price)
        # Average is over the shares we DO know the cost of — not 233/2.06.
        self.assertEqual(h.avg_cost, known_cost / Decimal("1.48"))
        self.assertLess(h.pnl_usd, 0, "the position is down; no phantom gain")
        self.assertEqual(h.market_value, self.price * self.live_shares)

    def test_summary_folds_untracked_value_into_invested(self):
        with mock.patch("wallbit.portfolio.get_live_snapshot", return_value=self.snapshot):
            summary = get_summary(self.user)

        untracked = self.live_shares - Decimal("1.48")
        self.assertEqual(
            summary.total_invested_usd, Decimal("233.06") + untracked * self.price
        )
        self.assertEqual(summary.current_value_usd, self.price * self.live_shares)
        self.assertEqual(summary.pnl_usd, summary.current_value_usd - summary.net_invested_usd)

    def test_once_synced_the_row_is_no_longer_pending(self):
        for _ in range(4):
            Investment.objects.create(
                user=self.user, kind=Investment.STOCK, action=Investment.BUY,
                symbol="SPCX", amount_usd=Decimal("20.00"), shares=Decimal("0.14"),
                executed_at=timezone.now(),
            )
        with mock.patch("wallbit.portfolio.get_live_snapshot", return_value=self.snapshot):
            h = get_holdings(self.user)[0]

        self.assertFalse(h.cost_pending)
        self.assertEqual(h.cost_basis, Decimal("313.06"))
        self.assertEqual(h.avg_cost, Decimal("313.06") / self.live_shares)
