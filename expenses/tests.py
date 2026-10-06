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
    """``compute_balance``: saldo determinista del mes actual por defecto, con el
    saldo inicial como ancla por moneda y corte en el instante en que se declaró."""

    TODAY = datetime(2026, 10, 6).date()

    def setUp(self):
        from unittest import mock

        from expenses import balance

        self.user = User.objects.create(
            external_id="573001110002", platform="whatsapp", first_name="Ana",
            default_currency="COP", timezone="America/Bogota",
        )
        self.tz = pytz.timezone("America/Bogota")
        self.today = timezone.now().astimezone(self.tz).date()
        tz, fixed = self.tz, self.TODAY

        class FixedNow(datetime):
            @classmethod
            def now(cls, tz_=None):
                return tz.localize(datetime(fixed.year, fixed.month, fixed.day, 18, 0))

        patcher = mock.patch.object(balance, "datetime", FixedNow)
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def _d(self, month, day):
        return datetime(2026, month, day).date()

    def test_sin_fechas_es_el_mes_actual_y_lo_dice(self):
        from expenses.balance import compute_balance

        self._income("1660000", self._d(8, 27))
        self._expense("500000", self._d(9, 20))
        self._income("200000", self._d(10, 5))
        self._expense("50000", self._d(10, 2))
        self._expense("9000", self._d(10, 7))  # futuro: fuera del período
        data = compute_balance(self.user)
        row = self._row(data)
        self.assertEqual(row["balance"], 150000.0)
        self.assertEqual(data["period"]["from"], "2026-10-01")
        self.assertEqual(data["period"]["to"], "2026-10-06")
        self.assertTrue(data["period"]["current_month"])
        self.assertIn("1 de octubre de 2026", data["period"]["label"])
        self.assertIn("6 de octubre de 2026", data["period"]["label"])

    def test_todo_el_historial_resta_con_exactitud(self):
        from expenses.balance import compute_balance

        self._income("1660000", self._d(8, 27))
        self._expense("1752900", self._d(10, 3))
        row = self._row(compute_balance(self.user, whole_history=True))
        self.assertEqual(row["balance"], -92900.0)  # el modelo había dicho −89.900
        self.assertIsNone(row["since_initial_balance"])

    def test_saldo_inicial_del_mes_descarta_lo_anterior_incluso_del_mismo_dia(self):
        from expenses.balance import compute_balance

        self._expense("500000", self._d(10, 1))
        self._expense("200", self.TODAY)  # registrado antes de declarar el saldo
        self._income("1000", self.TODAY, note="saldo inicial")
        self._expense("50", self.TODAY, dated=False)  # después, sin fecha real
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], 950.0)
        self.assertEqual(row["since_initial_balance"], self.TODAY.isoformat())
        self.assertEqual(self._row(compute_balance(self.user, whole_history=True))["balance"], -499250.0)

    def test_el_saldo_inicial_es_por_moneda(self):
        from expenses.balance import compute_balance

        self._income("100", self._d(10, 5), currency="USD", note="saldo inicial")
        self._expense("10", self.TODAY, currency="USD")
        self._expense("300", self._d(10, 5))
        self._income("1000", self.TODAY, note="saldo inicial")
        data = compute_balance(self.user)
        self.assertEqual(self._row(data, "USD")["balance"], 90.0)
        self.assertEqual(self._row(data, "COP")["balance"], 1000.0)

    def test_saldo_inicial_sin_fecha_real_y_el_ultimo_reemplaza_al_anterior(self):
        from expenses.balance import compute_balance

        self._income("5000", self._d(10, 2), note="Saldo inicial")
        self._expense("100", self._d(10, 5))
        self._income("1000", self.TODAY, note="saldo inicial", dated=False)
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], 1000.0)
        self.assertEqual(row["incomes_count"], 1)

    def test_saldo_inicial_anterior_al_periodo_no_cuenta(self):
        # El saldo es el del período; un saldo inicial de hace más de dos días
        # antes del mes no entra en el saldo de octubre.
        from expenses.balance import compute_balance

        self._income("1660000", self._d(9, 28), note="saldo inicial")
        self._expense("1321000", self._d(10, 3))
        self._income("200000", self._d(10, 5))
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], -1121000.0)
        self.assertIsNone(row["since_initial_balance"])
        self.assertFalse(row["carried_initial_balance"])

    def test_saldo_inicial_de_los_dos_dias_previos_se_arrastra(self):
        # Usuario 128: declaró 1.660.000 el 30-09; su saldo de octubre parte de ahí.
        from expenses.balance import compute_balance

        self._expense("984500", self._d(5, 29))
        self._expense("300", self._d(9, 30))  # registrado antes de declarar: no cuenta
        self._income("1660000", self._d(9, 30), note="saldo inicial")
        self._expense("1301000", self._d(10, 3))
        self._income("200000", self._d(10, 5))
        self._expense("112000", self.TODAY)
        for data in (compute_balance(self.user),
                     compute_balance(self.user, start_date="2026-10-01", end_date="2026-10-06")):
            row = self._row(data)
            self.assertEqual(row["balance"], 447000.0)
            self.assertEqual(row["since_initial_balance"], "2026-09-30")
            self.assertTrue(row["carried_initial_balance"])
            self.assertEqual(row["incomes_count"], 2)
            self.assertIn("30 de septiembre de 2026 (1.660.000 COP)", row["counted_label"])

    def test_lo_posterior_al_saldo_arrastrado_del_mes_anterior_cuenta(self):
        from expenses.balance import compute_balance

        self._income("1000", self._d(9, 29), note="saldo inicial")
        self._expense("100", self._d(9, 30))
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], 900.0)
        self.assertEqual(row["since_initial_balance"], "2026-09-29")

    def test_un_saldo_inicial_del_mes_gana_al_arrastrado(self):
        from expenses.balance import compute_balance

        self._income("1000", self._d(9, 30), note="saldo inicial")
        self._income("500", self._d(10, 2), note="saldo inicial")
        self._expense("50", self._d(10, 4))
        row = self._row(compute_balance(self.user))
        self.assertEqual(row["balance"], 450.0)
        self.assertFalse(row["carried_initial_balance"])

    def test_sin_arrastre_si_el_periodo_no_empieza_el_dia_1(self):
        from expenses.balance import compute_balance

        self._income("1000", self._d(10, 1), note="saldo inicial")
        self._expense("50", self._d(10, 4))
        row = self._row(compute_balance(self.user, start_date="2026-10-03"))
        self.assertEqual(row["balance"], -50.0)
        self.assertFalse(row["carried_initial_balance"])

    def test_arrastre_por_moneda(self):
        from expenses.balance import compute_balance

        self._income("100", self._d(9, 30), currency="USD", note="saldo inicial")
        self._expense("10", self._d(10, 2), currency="USD")
        self._expense("300", self._d(10, 2))
        data = compute_balance(self.user)
        self.assertEqual(self._row(data, "USD")["balance"], 90.0)
        self.assertTrue(self._row(data, "USD")["carried_initial_balance"])
        self.assertEqual(self._row(data, "COP")["balance"], -300.0)
        self.assertFalse(self._row(data, "COP")["carried_initial_balance"])

    def test_solo_fecha_de_corte_cuenta_desde_el_saldo_inicial(self):
        # 2026-10-05: el agente pidió el saldo con end_date=hoy y salió −445.500
        # (todo el historial) en vez de 539.000 desde el saldo inicial.
        from expenses.balance import compute_balance

        self._expense("984500", self._d(5, 29))
        self._income("1660000", self._d(9, 30), note="saldo inicial")
        self._expense("1321000", self._d(10, 3))
        self._income("200000", self._d(10, 5))
        self._expense("112000", self.TODAY)
        data = compute_balance(self.user, end_date="2026-10-05")
        row = self._row(data)
        self.assertEqual(row["balance"], 539000.0)
        self.assertEqual(row["since_initial_balance"], "2026-09-30")
        self.assertIn("hasta el 5 de octubre de 2026", data["period"]["label"])

    def test_fecha_de_corte_anterior_al_saldo_inicial_lo_ignora(self):
        from expenses.balance import compute_balance

        self._expense("100", self._d(9, 20))
        self._income("1000", self._d(10, 4), note="saldo inicial")
        row = self._row(compute_balance(self.user, end_date="2026-10-01"))
        self.assertEqual(row["balance"], -100.0)
        self.assertIsNone(row["since_initial_balance"])

    def test_periodo_explicito_con_saldo_inicial_dentro(self):
        from expenses.balance import compute_balance

        self._expense("999", self._d(10, 1))
        self._income("1000", self._d(10, 2), note="saldo inicial")
        self._expense("300", self.TODAY)
        data = compute_balance(self.user, start_date="2026-10-01", end_date="2026-10-06")
        self.assertEqual(self._row(data)["balance"], 700.0)
        self.assertEqual(self._row(data)["since_initial_balance"], "2026-10-02")
        self.assertFalse(data["period"]["current_month"])

    def test_solo_fecha_inicial_llega_hasta_hoy(self):
        from expenses.balance import compute_balance

        self._expense("100", self._d(10, 2))
        self._expense("5000", self._d(10, 15))  # futuro
        self._income("9000", self._d(10, 20), note="saldo inicial")  # ancla futura
        data = compute_balance(self.user, start_date="2026-10-01")
        self.assertEqual(self._row(data)["balance"], -100.0)
        self.assertEqual(data["period"]["to"], "2026-10-06")
        self.assertIsNone(self._row(data)["since_initial_balance"])

    def test_rango_invertido_es_un_error(self):
        from expenses.balance import compute_balance

        self._income("1000", self._d(9, 29), note="saldo inicial")
        self._expense("100", self._d(9, 30))
        for kwargs in ({"start_date": "2026-10-01", "end_date": "2026-09-30"},
                       {"start_date": "2026-11-01"}):  # inicio futuro: llega hasta hoy
            with self.assertRaises(ValueError):
                compute_balance(self.user, **kwargs)

    def test_una_nota_que_solo_menciona_saldo_inicial_no_es_ancla(self):
        from expenses.balance import compute_balance

        self._expense("100", self._d(10, 3))
        self._income("1000", self.TODAY, note="Saldo inicial del mes registrado como pago")
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
        # set_initial_balance usa el reloj real; el período va explícito.
        row = self._row(compute_balance(
            self.user, start_date=self.today.replace(day=1).isoformat(), end_date=self.today.isoformat()
        ))
        self.assertEqual(row["balance"], -25.0)
        self.assertEqual(row["initial_balance"], 0.0)
        with self.assertRaises(ValueError):
            set_initial_balance(self.user, Decimal("-1"))
