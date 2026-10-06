"""Un movimiento = un registro, y sin fecha del usuario es de hoy.

    python manage.py test agents.tests.test_duplicate_guard

Casos reales (2026-10-02 y 2026-10-05): "20000 cervezas" y "200000 del
préstamo Darwin ya me los devolvió" quedaron registrados dos veces, uno con
la fecha de hoy y otro con un "hoy, <fecha>" copiado del historial.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from agents.date_guard import current_turn_texts, memory_user_texts, resolve_new_record_date
from agents.duplicate_guard import TurnCreations, amount_mentions, asks_for_repetition

TODAY = date(2026, 10, 5)


class AmountMentionsTests(SimpleTestCase):
    def test_formatos(self):
        self.assertEqual(amount_mentions("200000 del préstamo Darwin", Decimal("200000")), 1)
        self.assertEqual(amount_mentions("200.000 almuerzo", Decimal("200000")), 1)
        self.assertEqual(amount_mentions("1,250,000 arriendo", Decimal("1250000")), 1)
        self.assertEqual(amount_mentions("9,99 café", Decimal("9.99")), 1)
        self.assertEqual(amount_mentions("20000 almuerzo y 20000 taxi", Decimal("20000")), 2)
        # Abreviados y escala coloquial: cuentan como el monto (revisión de Codex).
        self.assertEqual(amount_mentions("20k almuerzo y 20k taxi", Decimal("20000")), 2)
        self.assertEqual(amount_mentions("20 mil almuerzo y 20 mil taxi", Decimal("20000")), 2)
        self.assertEqual(amount_mentions("20 almuerzo y 20 taxi", Decimal("20000")), 2)
        self.assertEqual(amount_mentions("1,5M carro", Decimal("1500000")), 1)

    def test_repeticion_solo_afirmativa(self):
        self.assertTrue(asks_for_repetition("3 cervezas de 20000 cada una"))
        self.assertTrue(asks_for_repetition("regístralo dos veces"))
        self.assertTrue(asks_for_repetition("20000 x3"))
        for text in ("registra 20000 taxi sin duplicar", "a veces tomo taxi: registra 20000",
                     "no lo registres dos veces", "pagué 20000 por los dos"):
            self.assertFalse(asks_for_repetition(text), text)


class TurnCreationsTests(SimpleTestCase):
    def _ok(self):
        return "Gasto registrado: 20000 COP"

    def test_un_monto_se_registra_una_vez(self):
        turn = TurnCreations("20000  cervezas")
        self.assertFalse(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))
        second = turn.create("expense", 20000.0, "COP", self._ok)
        self.assertTrue(second.startswith("Error: NO registrado"))

    def test_montos_y_tipos_distintos_pasan(self):
        turn = TurnCreations("20000 almuerzo y 5000 propina")
        self.assertFalse(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))
        self.assertFalse(turn.create("expense", 5000, "COP", self._ok).startswith("Error"))
        self.assertFalse(turn.create("income", 20000, "COP", self._ok).startswith("Error"))
        self.assertFalse(turn.create("expense", 20000, "USD", self._ok).startswith("Error"))

    def test_el_monto_escrito_dos_veces_permite_dos(self):
        turn = TurnCreations("20000 almuerzo y 20000 taxi")
        self.assertFalse(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))
        self.assertFalse(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))
        self.assertTrue(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))

    def test_abreviados_permiten_los_dos(self):
        turn = TurnCreations("20k almuerzo y 20k taxi")
        self.assertFalse(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))
        self.assertFalse(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))

    def test_repeticion_explicita_no_tiene_tope(self):
        turn = TurnCreations("registra 3 cervezas de 20000 cada una")
        for _ in range(3):
            self.assertFalse(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))

    def test_un_error_no_cuenta_como_registrado(self):
        turn = TurnCreations("20000 cervezas")
        self.assertTrue(turn.create("expense", 20000, "COP", lambda: "Error: límite del plan").startswith("Error"))
        self.assertFalse(turn.create("expense", 20000, "COP", self._ok).startswith("Error"))

    def test_llamadas_en_paralelo_crean_una(self):
        # La primera creación queda bloqueada hasta que la segunda llamada ya
        # entró: sin el candado, las dos crearían.
        turn = TurnCreations("200000 del préstamo Darwin ya me los devolvió")
        created, inside, release = [], threading.Event(), threading.Event()
        results = []

        def slow_create():
            created.append(1)
            inside.set()
            release.wait(2)
            return "Ingreso registrado"

        first = threading.Thread(target=lambda: results.append(turn.create("income", 200000, "COP", slow_create)))
        first.start()
        inside.wait(2)
        second = threading.Thread(target=lambda: results.append(turn.create("income", 200000, "COP", slow_create)))
        second.start()
        second.join(0.3)
        self.assertTrue(second.is_alive())  # espera el candado, no crea
        release.set()
        first.join(2)
        second.join(2)
        self.assertEqual(len(created), 1)
        self.assertEqual(sum(r.startswith("Error: NO registrado") for r in results), 1)


class NewRecordDateTests(SimpleTestCase):
    def test_sin_dia_del_usuario_es_hoy(self):
        texts = ["200000 del préstamo Darwin ya me los devolvió"]
        self.assertEqual(resolve_new_record_date("2026-10-04", TODAY, texts), "2026-10-05")
        self.assertIsNone(resolve_new_record_date(None, TODAY, texts))
        self.assertEqual(resolve_new_record_date("2026-10-05", TODAY, texts), "2026-10-05")
        # "mil" no es una fecha (revisión de Codex)
        self.assertEqual(resolve_new_record_date("2026-10-01", TODAY, ["20 mil taxi"]), "2026-10-05")

    def test_con_dia_dado_se_respeta(self):
        for text in ("20000 cervezas ayer", "el sábado 20000 cine", "20000 el 3 de octubre",
                     "anoche 20000 taxi", "20000 cena del viernes", "20000 taxi el 5",
                     "el primero pagué 20000", "el quince 30000 mercado", "hace 3 días 20000"):
            self.assertEqual(resolve_new_record_date("2026-10-03", TODAY, [text]), "2026-10-03", text)

    def test_con_dia_dado_solo_corrige_el_año(self):
        self.assertEqual(resolve_new_record_date("2023-10-04", TODAY, ["ayer 12000 gaseosa"]), "2026-10-04")

    def test_textos_del_turno(self):
        # El anterior entra solo si uno de los dos está incompleto.
        self.assertEqual(current_turn_texts("200000 Darwin", "ayer 8000 papas"), ["200000 Darwin"])
        self.assertEqual(current_turn_texts("20000", "ayer gasté en taxi"), ["20000", "ayer gasté en taxi"])
        self.assertEqual(current_turn_texts("COP", "20000 taxi ayer"), ["COP", "20000 taxi ayer"])
        self.assertEqual(current_turn_texts("20000 taxi", None), ["20000 taxi"])

    def test_memoria_sin_metadatos_ni_tresqu(self):
        lines = ["[2026-10-01] Tresqu: Registré 20000 hoy 1 de octubre",
                 "[2024-03-16] Usuario: Viajé a Lima el 15/03/2024"]
        self.assertEqual(memory_user_texts(lines), ["Viajé a Lima el 15/03/2024"])


class ExpensesToolsWiringTests(SimpleTestCase):
    """Las tools del subagente aplican las dos guardas."""

    def _tools(self, user_message, user_context, day_context=None):
        from agents.subagents import expenses as subagent

        user = SimpleNamespace(external_id="x", default_currency="COP", timezone="America/Bogota")
        return {t.name: t for t in subagent.build_expenses_tools(
            user, "", "", (user_message,), user_context, user_message, day_context)}

    def test_caso_darwin(self):
        from agents.subagents import expenses as subagent

        message = "200000 del préstamo Darwin ya me los devolvió"
        # Un "ayer" de un mensaje anterior con su propio monto no es de este turno.
        tools = self._tools(message, ["ayer 8000 papas", message], current_turn_texts(message, "ayer 8000 papas"))
        sent = []

        def fake_invoke(tool, payload):
            sent.append(payload)
            return f"Ingreso registrado ({payload['received_at']})"

        with mock.patch.object(subagent, "_invoke_strict", fake_invoke), \
                mock.patch("agents.subagents.expenses.datetime") as fake_dt:
            fake_dt.now.return_value = SimpleNamespace(date=lambda: TODAY)
            first = tools["create_income_for_user"].invoke(
                {"amount": 200000, "category": "Otros", "received_at": "2026-10-04"})
            second = tools["create_income_for_user"].invoke(
                {"amount": 200000, "category": "Otros", "received_at": None})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["received_at"], "2026-10-05")
        self.assertIn("registrado", first)
        self.assertTrue(second.startswith("Error: NO registrado"))
