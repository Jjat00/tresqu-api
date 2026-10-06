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


class BalanceTests(TestCase):
    """``compute_balance``: saldo determinista, con el saldo inicial como ancla
    por moneda y corte en el instante en que se declaró."""

    def setUp(self):
        self.user = User.objects.create(
            external_id="573001110002", platform="whatsapp", first_name="Ana",
            default_currency="COP", timezone="America/Bogota",
        )
        self.tz = pytz.timezone("America/Bogota")
        self.today = timezone.now().astimezone(self.tz).date()

    def _ts(self, day, hour=12):
        return self.tz.localize(datetime(day.year, day.month, day.day, hour, 0)).astimezone(dt_timezone.utc)

    def _expense(self, amount, day, currency="COP", dated=True):
        return Expense.objects.create(
            user=self.user, amount=Decimal(amount), currency=currency, description="x",
            timestamp=self._ts(day), spent_at=day if dated else None,
        )

    def _income(self, amount, day, currency="COP", note="", dated=True):
        return Income.objects.create(
            user=self.user, amount=Decimal(amount), currency=currency, description="y",
            note=note, timestamp=self._ts(day), received_at=day if dated else None,
        )

    def _row(self, data, currency="COP"):
        return next(r for r in data["by_currency"] if r["currency"] == currency)

    def test_sin_saldo_inicial_cuenta_todo_lo_registrado(self):
        from datetime import timedelta
        from expenses.balance import compute_balance

        self._income("1660000", self.today - timedelta(days=40))
        self._expense("1752900", self.today - timedelta(days=3))
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], -92900.0)  # el modelo había dicho −89.900
        self.assertIsNone(row["since_initial_balance"])

    def test_saldo_inicial_descarta_lo_anterior_incluso_del_mismo_dia(self):
        from datetime import timedelta
        from expenses.balance import compute_balance

        self._expense("500000", self.today - timedelta(days=200))
        self._expense("200", self.today)  # registrado antes de declarar el saldo
        self._income("1000", self.today, note="saldo inicial")
        self._expense("50", self.today, dated=False)  # después, sin fecha real
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], 950.0)
        self.assertEqual(row["since_initial_balance"], self.today.isoformat())
        self.assertEqual(self._row(compute_balance(self.user, whole_history=True))["balance"], -499250.0)

    def test_el_saldo_inicial_es_por_moneda(self):
        from datetime import timedelta
        from expenses.balance import compute_balance

        yesterday = self.today - timedelta(days=1)
        self._income("100", yesterday, currency="USD", note="saldo inicial")
        self._expense("10", self.today, currency="USD")
        self._expense("300", yesterday)
        self._income("1000", self.today, note="saldo inicial")
        data = compute_balance(self.user)
        self.assertEqual(self._row(data, "USD")["balance"], 90.0)
        self.assertEqual(self._row(data, "COP")["balance"], 1000.0)

    def test_saldo_inicial_sin_fecha_real_y_el_ultimo_reemplaza_al_anterior(self):
        from datetime import timedelta
        from expenses.balance import compute_balance

        self._income("5000", self.today - timedelta(days=10), note="Saldo inicial")
        self._expense("100", self.today - timedelta(days=1))
        self._income("1000", self.today, note="saldo inicial", dated=False)
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], 1000.0)
        self.assertEqual(row["incomes_count"], 1)

    def test_con_fechas_no_usa_anclas(self):
        from datetime import timedelta
        from expenses.balance import compute_balance

        start = self.today - timedelta(days=5)
        self._income("1000", start, note="saldo inicial")
        self._expense("300", self.today)
        data = compute_balance(self.user, start_date=start.isoformat(), end_date=self.today.isoformat())
        self.assertEqual(self._row(data)["balance"], 700.0)
        self.assertIsNone(self._row(data)["since_initial_balance"])

    def test_solo_fecha_de_corte_sigue_contando_desde_el_saldo_inicial(self):
        # 2026-10-05: el agente pidió el saldo con end_date=hoy y salió −445.500
        # (todo el historial) en vez de 539.000 desde el saldo inicial.
        from datetime import timedelta
        from expenses.balance import compute_balance

        self._expense("984500", self.today - timedelta(days=300))
        self._income("1660000", self.today - timedelta(days=6), note="saldo inicial")
        self._expense("1321000", self.today - timedelta(days=3))
        self._income("200000", self.today - timedelta(days=1))
        self._expense("112000", self.today)
        row = self._row(compute_balance(self.user, end_date=(self.today - timedelta(days=1)).isoformat()))
        self.assertEqual(row["balance"], 539000.0)
        self.assertEqual(row["since_initial_balance"], (self.today - timedelta(days=6)).isoformat())

    def test_fecha_de_corte_anterior_al_saldo_inicial_usa_el_ancla_previa_o_ninguna(self):
        from datetime import timedelta
        from expenses.balance import compute_balance

        self._expense("100", self.today - timedelta(days=10))
        self._income("1000", self.today - timedelta(days=2), note="saldo inicial")
        row = self._row(compute_balance(self.user, end_date=(self.today - timedelta(days=5)).isoformat()))
        self.assertEqual(row["balance"], -100.0)
        self.assertIsNone(row["since_initial_balance"])

    def test_una_nota_que_solo_menciona_saldo_inicial_no_es_ancla(self):
        from datetime import timedelta
        from expenses.balance import compute_balance

        self._expense("100", self.today - timedelta(days=3))
        self._income("1000", self.today, note="Saldo inicial del mes registrado como pago")
        self.assertEqual(self._row(compute_balance(self.user))["balance"], 900.0)

    def test_fijar_saldo_inicial_acepta_cero_y_ancla_desde_ahi(self):
        from datetime import timedelta
        from unittest import mock

        from agents.run_context import start_transaction_tracking
        from expenses.balance import compute_balance, set_initial_balance

        self._expense("700", self.today - timedelta(days=2))
        tracked = start_transaction_tracking()
        with mock.patch("telegrambot.tools.embeddings") as fake_embeddings:
            fake_embeddings.embed_query.return_value = None
            income = set_initial_balance(self.user, Decimal("0"))
        self.assertIn({"kind": "income", "id": income.id}, tracked)
        self.assertEqual(income.user_income_category.name, "Saldo Inicial")
        with self.assertRaises(ValueError):
            set_initial_balance(self.user, Decimal("10"), currency="USDT")
        self._expense("25", self.today, dated=False)
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], -25.0)
        self.assertEqual(row["initial_balance"], 0.0)
        with self.assertRaises(ValueError):
            set_initial_balance(self.user, Decimal("-1"))
