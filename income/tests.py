from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from income.models import Income
from users.models import User


def _utc(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=dt_timezone.utc)


class MonthSummaryTests(TransactionTestCase):
    """`/api/incomes/month_summary/` alimenta la tabla de ingresos del dashboard:
    devuelve cada movimiento (para poder editarlo) y los totales por moneda."""

    def setUp(self):
        self.user = User.objects.create(
            external_id="573001112222", platform="whatsapp", first_name="Jaime",
            default_currency="COP", timezone="America/Bogota",
        )
        I = lambda amount, received, ts, cur="COP": Income.objects.create(
            user=self.user, amount=Decimal(amount), currency=cur,
            description="x", received_at=received, timestamp=ts,
        )
        # dentro: recibido el 01/09 aunque registrado en agosto
        I("6000000", date(2026, 9, 1), _utc(2026, 8, 31))
        # dentro: 30/09 a las 23:00 hora Bogotá (01/10 04:00 UTC)
        I("250000", date(2026, 9, 30), _utc(2026, 10, 1, 4))
        # dentro, otra moneda
        I("300", date(2026, 9, 10), _utc(2026, 9, 10), cur="USD")
        # dentro por fallback: sin received_at, timestamp de septiembre
        I("70000", None, _utc(2026, 9, 15))
        # fuera: agosto
        I("999999", date(2026, 8, 31), _utc(2026, 8, 31))
        # fuera por fallback: sin received_at, 31/08 23:59 Bogotá (01/09 04:59 UTC)
        I("888888", None, _utc(2026, 9, 1, 4, 59))

        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")

    def _get(self, **params):
        response = self.client.get("/api/incomes/month_summary/", params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_devuelve_solo_los_ingresos_del_mes(self):
        data = self._get(month=9, year=2026)

        amounts = sorted(float(i["amount"]) for i in data["incomes"])
        self.assertEqual(amounts, [300.0, 70000.0, 250000.0, 6000000.0])

    def test_totales_separados_por_moneda(self):
        data = self._get(month=9, year=2026)

        self.assertEqual(data["totals_by_currency"], {"COP": 6320000.0, "USD": 300.0})

    def test_cada_ingreso_trae_lo_necesario_para_editarlo(self):
        data = self._get(month=9, year=2026)

        income = next(i for i in data["incomes"] if float(i["amount"]) == 300.0)
        self.assertEqual(income["currency"], "USD")
        self.assertEqual(income["received_at"], "2026-09-10")
        self.assertIn("id", income)
        self.assertIn("current_category", income)

    def test_mes_invalido_responde_400(self):
        response = self.client.get(
            "/api/incomes/month_summary/", {"month": 13, "year": 2026})
        self.assertEqual(response.status_code, 400)

    def test_no_ve_ingresos_de_otro_usuario(self):
        otro = User.objects.create(
            external_id="573009998888", platform="whatsapp", first_name="Otro",
            default_currency="COP", timezone="America/Bogota",
        )
        Income.objects.create(
            user=otro, amount=Decimal("1234"), currency="COP", description="x",
            received_at=date(2026, 9, 5), timestamp=_utc(2026, 9, 5),
        )

        data = self._get(month=9, year=2026)

        self.assertNotIn(1234.0, [float(i["amount"]) for i in data["incomes"]])
