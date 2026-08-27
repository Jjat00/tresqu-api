from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase

from expenses.models import Expense
from income.models import Income
from telegrambot.tools import get_expense_totals, get_expenses_by_user, get_income_totals
from users.models import User


def _ts(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 15, 0, tzinfo=dt_timezone.utc)


class ExpenseQueryToolsTests(TestCase):
    """Las tools de consulta del agente deben responder lo mismo que el dashboard:
    filtro por fecha del gasto (spent_at) y totales por moneda calculados en BD."""

    def setUp(self):
        self.user = User.objects.create(
            external_id="573001110000", platform="whatsapp", first_name="Jaime",
            default_currency="COP", timezone="America/Bogota",
        )
        rows = [
            (Decimal("1000"), "COP", date(2026, 7, 31)),   # fuera del período
            (Decimal("2500"), "COP", date(2026, 8, 1)),
            (Decimal("4000"), "COP", date(2026, 8, 15)),
            (Decimal("10.50"), "USD", date(2026, 8, 15)),
            (Decimal("7000"), "COP", date(2026, 8, 28)),   # fuera del período
        ]
        for amount, currency, spent in rows:
            Expense.objects.create(
                user=self.user, amount=amount, currency=currency,
                description="x", timestamp=_ts(spent), spent_at=spent,
            )
        Income.objects.create(
            user=self.user, amount=Decimal("900000"), currency="COP",
            description="salario", timestamp=_ts(date(2026, 8, 5)), received_at=date(2026, 8, 5),
        )

    def _call(self, tool, **kwargs):
        return tool.invoke({"user_external_id": self.user.external_id, **kwargs})

    def test_totales_de_gastos_por_moneda_en_el_periodo(self):
        result = self._call(get_expense_totals, start_date="2026-08-01", end_date="2026-08-27")
        by_currency = {t["currency"]: t for t in result["totals"]}
        self.assertEqual(by_currency["COP"], {"currency": "COP", "total": 6500.0, "count": 2})
        self.assertEqual(by_currency["USD"], {"currency": "USD", "total": 10.5, "count": 1})

    def test_totales_sin_fechas_es_el_historico(self):
        result = self._call(get_expense_totals)
        by_currency = {t["currency"]: t["total"] for t in result["totals"]}
        self.assertEqual(by_currency["COP"], 14500.0)

    def test_totales_de_ingresos(self):
        result = self._call(get_income_totals, start_date="2026-08-01", end_date="2026-08-31")
        self.assertEqual(result["totals"], [{"currency": "COP", "total": 900000.0, "count": 1}])

    def test_listar_gastos_respeta_el_rango_de_fechas(self):
        rows = self._call(get_expenses_by_user, start_date="2026-08-01", end_date="2026-08-27")
        self.assertEqual([r["spent_at"] for r in rows], ["2026-08-15", "2026-08-15", "2026-08-01"])

    def test_listar_gastos_sin_rango_devuelve_todo_con_tope(self):
        self.assertEqual(len(self._call(get_expenses_by_user)), 5)
        self.assertEqual(len(self._call(get_expenses_by_user, limit=2)), 2)

    def test_usuario_inexistente(self):
        self.assertEqual(get_expense_totals.invoke({"user_external_id": "nadie"}), {"error": "Usuario no encontrado"})
