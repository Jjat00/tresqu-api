"""Tests del guard de moneda.

Origen (2026-09-04): "Recibí 6M de ingresos de frostbyte" por WhatsApp quedó
registrado como 6.000.000 **USD** con la moneda por defecto del usuario en COP.
El prompt ya prohibía inferir la moneda; hacía falta una defensa determinista.
"""
from types import SimpleNamespace

from django.test import SimpleTestCase
from langchain_core.tools import tool

from agents import currency_guard
from agents.currency_guard import (
    conversation_texts,
    mentioned_currency,
    mentions_currency,
    resolve_currency,
)
from agents.subagents import expenses as subagent


class MentionsCurrencyTests(SimpleTestCase):
    def test_reconoce_codigo_iso_y_alias(self):
        self.assertTrue(mentions_currency("me pagaron 50 USD", "USD"))
        self.assertTrue(mentions_currency("me pagaron 50 dólares", "USD"))
        self.assertTrue(mentions_currency("50 Dolares del proyecto", "USD"))
        self.assertTrue(mentions_currency("cobré 30 euros", "EUR"))
        self.assertTrue(mentions_currency("son 20.000 pesos colombianos", "COP"))
        self.assertTrue(mentions_currency("gasté 500 MXN", "MXN"))

    def test_palabras_ambiguas_no_cuentan(self):
        self.assertFalse(mentions_currency("me pagaron 6M", "USD"))
        self.assertFalse(mentions_currency("me dieron 20 mil pesos", "COP"))
        self.assertFalse(mentions_currency("gasté $90", "USD"))

    def test_no_hace_match_parcial(self):
        self.assertFalse(mentions_currency("compré 10 USDT", "USD"))
        self.assertFalse(mentions_currency("un copago de 30", "COP"))


class ResolveCurrencyTests(SimpleTestCase):
    def test_sin_moneda_pedida_cae_al_default(self):
        self.assertEqual(resolve_currency("", "COP", ["recibí 6M"]), "")
        self.assertEqual(resolve_currency(None, "COP", []), "")

    def test_descarta_la_moneda_que_nadie_menciono(self):
        self.assertEqual(
            resolve_currency("USD", "COP", ["Recibí 6M de ingresos de frostbyte"]),
            "",
        )

    def test_respeta_la_moneda_dicha_por_el_usuario(self):
        self.assertEqual(
            resolve_currency("USD", "COP", ["me pagaron 300 dólares del freelance"]),
            "USD",
        )

    def test_respeta_la_moneda_por_defecto_del_usuario(self):
        self.assertEqual(resolve_currency("cop", "COP", ["recibí 6M"]), "COP")

    def test_la_moneda_puede_venir_de_un_turno_anterior(self):
        contexto = ["¿lo registro como 100 USD?", "sí, dale"]
        self.assertEqual(resolve_currency("USD", "COP", contexto), "USD")


class MentionedCurrencyTests(SimpleTestCase):
    def test_edicion_sin_mencion_no_cambia_la_moneda(self):
        self.assertEqual(mentioned_currency("USD", ["cambia ese gasto a 90 mil"]), "")

    def test_edicion_con_mencion_aplica_la_moneda(self):
        self.assertEqual(
            mentioned_currency("USD", ["ese gasto fue en dólares, corrígelo"]),
            "USD",
        )


class ConversationTextsTests(SimpleTestCase):
    def test_incluye_historial_y_turno_actual(self):
        history = [
            SimpleNamespace(content="hola"),
            {"content": "¿lo registro como 100 USD?"},
            SimpleNamespace(content=None),
        ]
        self.assertEqual(
            conversation_texts("sí", history),
            ["hola", "¿lo registro como 100 USD?", "sí"],
        )


class ExpensesToolsCurrencyTests(SimpleTestCase):
    """El guard debe aplicarse dentro de las tools del subagente, que es donde
    llega la moneda alucinada por el supervisor o por el propio subagente."""

    def setUp(self):
        self.calls: list[dict] = []

        @tool
        def fake_create_income(
            user_external_id: str,
            amount: float,
            currency: str,
            category: str,
            received_at: str | None = None,
            note: str | None = "",
        ) -> str:
            """fake"""
            self.calls.append({"currency": currency, "amount": amount})
            return "ok"

        @tool
        def fake_create_expense(
            user_external_id: str,
            amount: float,
            currency: str,
            category: str,
            spent_at: str | None = None,
            note: str | None = "",
        ) -> str:
            """fake"""
            self.calls.append({"currency": currency, "amount": amount})
            return "ok"

        @tool
        def fake_get_or_create_income_category(
            name: str,
            description: str | None = None,
            example: str | None = None,
            color: str | None = None,
        ) -> str:
            """fake"""
            return "ok"

        self._originals = {
            "create_income": subagent.create_income,
            "create_expense": subagent.create_expense,
            "get_or_create_income_category": subagent.get_or_create_income_category,
        }
        subagent.create_income = fake_create_income
        subagent.create_expense = fake_create_expense
        subagent.get_or_create_income_category = fake_get_or_create_income_category

    def tearDown(self):
        for name, original in self._originals.items():
            setattr(subagent, name, original)

    def _tools(self, context):
        user = SimpleNamespace(external_id="u1", default_currency="COP")
        return {
            t.name: t
            for t in subagent.build_expenses_tools(user, "", "", context)
        }

    def test_ingreso_sin_moneda_dicha_se_guarda_en_la_del_usuario(self):
        tools = self._tools(["Recibí 6M de ingresos de frostbyte"])
        tools["create_income_for_user"].invoke({
            "amount": 6000000.0,
            "category": "Otros Ingresos",
            "currency": "USD",
        })
        self.assertEqual(self.calls[-1]["currency"], "")

    def test_ingreso_con_moneda_dicha_la_respeta(self):
        tools = self._tools(["me llegaron 300 dólares de frostbyte"])
        tools["create_income_for_user"].invoke({
            "amount": 300.0,
            "category": "Freelance",
            "currency": "USD",
        })
        self.assertEqual(self.calls[-1]["currency"], "USD")

    def test_gasto_sin_moneda_dicha_se_guarda_en_la_del_usuario(self):
        tools = self._tools(["gasté 90 en una camisa"])
        tools["create_expense_for_user"].invoke({
            "amount": 90000.0,
            "category": "Ropa",
            "currency": "USD",
        })
        self.assertEqual(self.calls[-1]["currency"], "")

    def test_las_tools_de_edicion_conservan_su_nombre(self):
        tools = self._tools([])
        self.assertIn("update_expense", tools)
        self.assertIn("update_income", tools)
