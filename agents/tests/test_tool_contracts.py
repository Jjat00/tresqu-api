"""Tests adversariales del contrato entre el subagente de gastos y sus tools.

Origen (2026-08-27): el agente respondía "gastaste 11,99M" cuando el dashboard
decía 8,24M. ``get_user_expenses(start_date, end_date)`` pasaba las fechas a una
tool que no las declaraba; LangChain las descartó en silencio, la tool devolvió
1.148 gastos históricos y el modelo sumó a mano.

Tres capas para que no vuelva a pasar:
1. ``_invoke_strict`` falla en runtime ante args no declarados.
2. Contrato estático (AST): todo wrapper usa ``_invoke_strict`` y solo pasa
   claves que la tool base declara.
3. Igualdad de punta a punta: la tool de totales del agente y el endpoint del
   KPI del dashboard responden lo mismo sobre un dataset con trampas.
"""
import ast
import inspect
import pathlib
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import SimpleTestCase, TransactionTestCase
from langchain_core.tools import tool
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from agents.subagents import expenses as subagent
from expenses.models import Expense
from telegrambot import tools as base_tools
from telegrambot.tools import get_expense_totals, get_expenses_by_user
from users.models import User


class StrictInvokeTests(SimpleTestCase):
    def test_acepta_args_declarados_y_rechaza_desconocidos(self):
        @tool
        def dummy(user_external_id: str) -> str:
            """dummy"""
            return f"ok {user_external_id}"

        self.assertEqual(subagent._invoke_strict(dummy, {"user_external_id": "u"}), "ok u")
        with self.assertRaises(TypeError) as ctx:
            subagent._invoke_strict(dummy, {"user_external_id": "u", "start_date": "2026-08-01", "end_date": "2026-08-27"})
        self.assertIn("start_date", str(ctx.exception))
        self.assertIn("end_date", str(ctx.exception))

    def test_langchain_sigue_descartando_en_silencio(self):
        """Documenta el comportamiento que motiva el guardia: si esto cambia, el
        guardia sigue siendo correcto, pero conviene saberlo."""
        @tool
        def dummy(user_external_id: str) -> str:
            """dummy"""
            return "sin error"

        self.assertEqual(dummy.invoke({"user_external_id": "u", "start_date": "x"}), "sin error")


class WrapperContractTests(SimpleTestCase):
    """Contrato estático: se lee el código fuente del subagente y se comprueba
    cada llamada a una tool base."""

    def _calls(self):
        src = pathlib.Path(inspect.getsourcefile(subagent)).read_text()
        return ast.parse(src)

    def test_ningun_wrapper_llama_invoke_directo(self):
        directos = [
            f"línea {node.lineno}: .{node.func.attr}("
            for node in ast.walk(self._calls())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("invoke", "ainvoke")
            and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "tool")
        ]
        self.assertEqual(directos, [], "usa _invoke_strict / _ainvoke_strict")

    def test_todos_los_wrappers_pasan_solo_args_declarados(self):
        problemas, revisadas = [], 0
        for node in ast.walk(self._calls()):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("_invoke_strict", "_ainvoke_strict")):
                continue
            if isinstance(node.args[0], ast.Name) and node.args[0].id == "tool":
                continue  # las definiciones genéricas del propio guardia
            tool_name = node.args[0].id
            payload = node.args[1]
            self.assertIsInstance(payload, ast.Dict, f"{tool_name}: el payload debe ser un dict literal")
            keys = {k.value for k in payload.keys}
            base = getattr(base_tools, tool_name)
            unknown = keys - set(base.args)
            if unknown:
                problemas.append(f"{tool_name} no declara {sorted(unknown)} (línea {node.lineno})")
            revisadas += 1
        self.assertEqual(problemas, [])
        self.assertGreaterEqual(revisadas, 20, "se esperaban al menos 20 wrappers revisados")


def _utc(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=dt_timezone.utc)


class AgentMatchesDashboardTests(TransactionTestCase):
    """La tool de totales del agente y el KPI del dashboard
    (``/api/expenses/donut_chart_data/``) deben coincidir sobre un dataset con
    trampas: registro en un mes y gasto en otro, bordes del rango a las 23:59 en
    hora local, movimientos sin ``spent_at`` y dos monedas."""

    RANGE = ("2026-08-01", "2026-08-27")

    def setUp(self):
        self.user = User.objects.create(
            external_id="573001119999", platform="whatsapp", first_name="Jaime",
            default_currency="COP", timezone="America/Bogota",
        )
        E = lambda amount, spent, ts, cur="COP": Expense.objects.create(
            user=self.user, amount=Decimal(amount), currency=cur, description="x",
            spent_at=spent, timestamp=ts,
        )
        # fuera: gastado el 31/07 aunque registrado el 01/08
        E("1000000", date(2026, 7, 31), _utc(2026, 8, 1, 3))
        # dentro: gastado el 01/08 aunque registrado el 31/07
        E("110000", date(2026, 8, 1), _utc(2026, 7, 31))
        # dentro: 27/08 a las 23:00 hora Bogotá (28/08 04:00 UTC)
        E("220000", date(2026, 8, 27), _utc(2026, 8, 28, 4))
        # fuera: 28/08
        E("3000000", date(2026, 8, 28), _utc(2026, 8, 28))
        # dentro por fallback: sin spent_at, timestamp 15/08
        E("330000", None, _utc(2026, 8, 15))
        # dentro por fallback: sin spent_at, 27/08 22:30 Bogotá (28/08 03:30 UTC)
        E("440000", None, _utc(2026, 8, 28, 3, 30))
        # fuera por fallback: sin spent_at, 31/07 23:59 Bogotá (01/08 04:59 UTC)
        E("5000000", None, _utc(2026, 8, 1, 4, 59))
        # dentro, otra moneda
        E("54.69", date(2026, 8, 10), _utc(2026, 8, 10), cur="USD")

        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")

    def test_totales_del_agente_igualan_al_kpi_del_dashboard(self):
        start, end = self.RANGE
        agent = get_expense_totals.invoke({"user_external_id": self.user.external_id, "start_date": start, "end_date": end})
        agent_totals = {t["currency"]: t["total"] for t in agent["totals"]}
        agent_count = sum(t["count"] for t in agent["totals"])

        r = self.client.get("/api/expenses/donut_chart_data/", {"date_filter": "custom", "start_date": start, "end_date": end})
        self.assertEqual(r.status_code, 200, r.content)

        self.assertEqual(agent_totals, r.data["totals_by_currency"])
        self.assertEqual(agent_count, r.data["total_count"])
        # y ambos son lo que un humano esperaría
        self.assertEqual(agent_totals, {"COP": 1100000.0, "USD": 54.69})
        self.assertEqual(agent_count, 5)

    def test_listado_del_agente_usa_el_mismo_criterio(self):
        start, end = self.RANGE
        rows = get_expenses_by_user.invoke({"user_external_id": self.user.external_id, "start_date": start, "end_date": end})
        self.assertEqual(sorted(r["amount"] for r in rows), [54.69, 110000.0, 220000.0, 330000.0, 440000.0])
