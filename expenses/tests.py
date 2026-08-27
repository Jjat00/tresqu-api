from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import pytz
from django.test import TestCase
from django.utils import timezone

from expenses.insights import compute_monthly_insights
from expenses.models import Expense
from income.models import Income
from users.models import User


class MonthlyInsightsCurrencyTests(TestCase):
    """Los totales del mes se calculan en la moneda por defecto del usuario; las
    demás monedas se reportan aparte en vez de sumarse como si fueran la misma."""

    def setUp(self):
        self.user = User.objects.create(
            external_id="573001110001", platform="whatsapp", first_name="Jaime",
            default_currency="COP", timezone="America/Bogota",
        )
        today = timezone.now().astimezone(pytz.timezone("America/Bogota")).date()
        ts = datetime(today.year, today.month, today.day, 12, 0, tzinfo=dt_timezone.utc)
        for amount, currency in [(Decimal("120000"), "COP"), (Decimal("80000"), "COP"), (Decimal("54.69"), "USD")]:
            Expense.objects.create(user=self.user, amount=amount, currency=currency,
                                   description="x", timestamp=ts, spent_at=today)
        Income.objects.create(user=self.user, amount=Decimal("25"), currency="USD",
                              description="y", timestamp=ts, received_at=today)

    def test_totales_solo_en_moneda_por_defecto_y_resto_aparte(self):
        data = compute_monthly_insights(self.user)

        self.assertEqual(data["currency"], "COP")
        self.assertEqual(data["totals"]["expenses"], 200000.0)
        self.assertEqual(data["totals"]["incomes"], 0.0)
        self.assertEqual(data["other_currencies"]["expenses"], [{"currency": "USD", "total": 54.69, "count": 1}])
        self.assertEqual(data["other_currencies"]["incomes"], [{"currency": "USD", "total": 25.0, "count": 1}])
