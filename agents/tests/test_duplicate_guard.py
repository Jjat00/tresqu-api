"""Un movimiento = un registro, y sin fecha del usuario es de hoy.

    python manage.py test agents.tests.test_duplicate_guard

Casos reales (2026-10-02 y 2026-10-05): "20000 cervezas" y "200000 del
préstamo Darwin ya me los devolvió" quedaron registrados dos veces, uno con
la fecha de hoy y otro con un "hoy, <fecha>" copiado del historial. El resto
son los falsos positivos que encontró Codex en tres rondas de revisión.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage

from agents.currency_guard import user_texts
from agents.date_guard import memory_user_texts, resolve_new_record_date
from agents.duplicate_guard import TurnCreations, amount_mentions, asks_for_repetition, note_key

TODAY = date(2026, 10, 5)


def _ok():
    return "Gasto registrado"


def _blocked(result: str) -> bool:
    return result.startswith("Error: NO registrado")


class HelpersTests(SimpleTestCase):
    def test_menciones_de_monto(self):
        self.assertEqual(amount_mentions("200000 del préstamo Darwin", Decimal("200000")), 1)
        self.assertEqual(amount_mentions("200.000 almuerzo", Decimal("200000")), 1)
        self.assertEqual(amount_mentions("1,250,000 arriendo", Decimal("1250000")), 1)
        self.assertEqual(amount_mentions("9,99 café", Decimal("9.99")), 1)
        self.assertEqual(amount_mentions("20k almuerzo y 20k taxi", Decimal("20000")), 2)
        self.assertEqual(amount_mentions("20 mil almuerzo y 20 mil taxi", Decimal("20000")), 2)

    def test_repeticion_solo_afirmativa(self):
        self.assertTrue(asks_for_repetition("3 cervezas de 20000 cada una"))
        self.assertTrue(asks_for_repetition("taxi y almuerzo, 20.000 COP cada uno"))
        self.assertTrue(asks_for_repetition("regístralo dos veces"))
        self.assertTrue(asks_for_repetition("20000 x3"))
        for text in ("registra 20000 taxi sin duplicar", "a veces tomo taxi: registra 20000",
                     "no lo registres dos veces", "pagué 20000 por los dos"):
            self.assertFalse(asks_for_repetition(text), text)

    def test_nota_normalizada(self):
        self.assertEqual(note_key("Devolución del préstamo de Darwin"), note_key("devolucion prestamo Darwin"))
        self.assertNotEqual(note_key("taxi"), note_key("almuerzo"))
        # Revisión de Codex, ronda 4: el orden distingue trayectos.
        self.assertNotEqual(note_key("taxi de casa a oficina"), note_key("taxi de oficina a casa"))


class TurnCreationsTests(SimpleTestCase):
    def test_incidente_mismo_movimiento_dos_veces(self):
        turn = TurnCreations("20000  cervezas")
        self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "cervezas", _ok)))
        self.assertTrue(_blocked(turn.create("expense", 20000.0, "COP", "Cervezas", _ok)))

    def test_movimientos_distintos_con_el_mismo_monto_pasan_siempre(self):
        # Sin interpretar el texto: abreviados, palabras, aclaraciones en cadena.
        for message, previous in (
            ("20k taxi y 20k almuerzo", None),
            ("Gasté veinte mil en taxi y veinte mil en almuerzo", None),
            ("sí", "COP"),
            ("el 5", "5k taxi y 5k almuerzo"),
        ):
            turn = TurnCreations(message, previous)
            self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "taxi", _ok)), message)
            self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "almuerzo", _ok)), message)

    def test_sin_nota_la_categoria_distingue(self):
        turn = TurnCreations("20k y 20k")
        self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "", _ok, "Transporte")))
        self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "", _ok, "Alimentación")))

    def test_tipo_y_moneda_distinguen(self):
        turn = TurnCreations("20000 taxi")
        self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "taxi", _ok)))
        self.assertFalse(_blocked(turn.create("income", 20000, "COP", "taxi", _ok)))
        self.assertFalse(_blocked(turn.create("expense", 20000, "USD", "taxi", _ok)))

    def test_identicos_permitidos_si_el_usuario_los_pidio(self):
        turn = TurnCreations("3 cervezas de 20000 cada una")
        for _ in range(3):
            self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "cerveza", _ok)))
        turn = TurnCreations("20000 almuerzo y 20000 almuerzo")
        self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "almuerzo", _ok)))
        self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "almuerzo", _ok)))
        self.assertTrue(_blocked(turn.create("expense", 20000, "COP", "almuerzo", _ok)))

    def test_un_error_no_cuenta_como_registrado(self):
        turn = TurnCreations("20000 cervezas")
        self.assertTrue(turn.create("expense", 20000, "COP", "cervezas", lambda: "Error: límite").startswith("Error"))
        self.assertFalse(_blocked(turn.create("expense", 20000, "COP", "cervezas", _ok)))

    def test_llamadas_en_paralelo_crean_una(self):
        # La primera creación queda bloqueada hasta que la segunda llamada ya
        # entró: sin el candado, las dos crearían.
        turn = TurnCreations("200000 del préstamo Darwin ya me los devolvió")
        note = "devolución del préstamo de Darwin"
        created, inside, release, results = [], threading.Event(), threading.Event(), []

        def slow_create():
            created.append(1)
            inside.set()
            release.wait(2)
            return "Ingreso registrado"

        first = threading.Thread(target=lambda: results.append(turn.create("income", 200000, "COP", note, slow_create)))
        first.start()
        inside.wait(2)
        second = threading.Thread(target=lambda: results.append(turn.create("income", 200000, "COP", note, slow_create)))
        second.start()
        second.join(0.3)
        self.assertTrue(second.is_alive())  # espera el candado, no crea
        release.set()
        first.join(2)
        second.join(2)
        self.assertEqual(len(created), 1)
        self.assertEqual(sum(_blocked(r) for r in results), 1)


class NewRecordDateTests(SimpleTestCase):
    def test_incidentes_sin_dia_del_usuario_es_hoy(self):
        # Lo que el usuario escribió en el historial visible de cada incidente.
        darwin = ["Cuanto dinero me queda", "Muéstrame el resumen detallado", "7000 arbitraje",
                  "10000 almuerzo", "8000 papas", "200000 del préstamo Darwin ya me los devolvió"]
        cervezas = ["12000 gaseosa", "12000 préstamo amigo", "29000 gasolina", "11000 Mouse hermano",
                    "5000 en mecato", "20000  cervezas"]
        self.assertEqual(resolve_new_record_date("2026-10-04", TODAY, darwin), "2026-10-05")
        self.assertEqual(resolve_new_record_date("2026-10-01", TODAY, cervezas), "2026-10-05")
        self.assertIsNone(resolve_new_record_date(None, TODAY, darwin))
        self.assertEqual(resolve_new_record_date("2026-10-01", TODAY, ["20 mil taxi"]), "2026-10-05")

    def test_con_dia_dado_se_respeta(self):
        for text in ("20000 cervezas ayer", "el sábado 20000 cine", "20000 el 3 de octubre",
                     "anoche 20000 taxi", "20000 cena del viernes", "20000 taxi el 5",
                     "el primero pagué 20000", "el quince 30000 mercado", "hace 3 días 20000",
                     "Gasté 20000 taxi dos días antes de hoy",
                     "El 5, gasté 20000 en taxi", "20000 taxi el 5."):
            self.assertEqual(resolve_new_record_date("2026-10-03", TODAY, [text]), "2026-10-03", text)

    def test_cadena_de_aclaraciones_conserva_el_dia(self):
        history = [HumanMessage(content="El 5 gasté en taxi"), AIMessage(content="¿Cuánto?"),
                   HumanMessage(content="20000"), AIMessage(content="¿En qué moneda?")]
        texts = user_texts("COP", history)
        self.assertEqual(resolve_new_record_date("2026-10-05", date(2026, 10, 6), texts), "2026-10-05")

    def test_con_dia_dado_solo_corrige_el_año(self):
        self.assertEqual(resolve_new_record_date("2023-10-04", TODAY, ["ayer 12000 gaseosa"]), "2026-10-04")

    def test_memoria_sin_metadatos_ni_tresqu(self):
        lines = ["[2026-10-01] Tresqu: Registré 20000 hoy 1 de octubre",
                 "[2024-03-16] Usuario: Viajé a Lima el 15/03/2024"]
        self.assertEqual(memory_user_texts(lines), ["Viajé a Lima el 15/03/2024"])


class ExpensesToolsWiringTests(SimpleTestCase):
    """Las tools del subagente aplican las dos guardas."""

    def test_caso_darwin(self):
        from agents.subagents import expenses as subagent

        message = "200000 del préstamo Darwin ya me los devolvió"
        user = SimpleNamespace(external_id="x", default_currency="COP", timezone="America/Bogota")
        texts = ["8000 papas", message]
        tools = {t.name: t for t in subagent.build_expenses_tools(
            user, "", "", (message,), texts, message, texts, "8000 papas")}
        sent = []

        def fake_invoke(tool, payload):
            sent.append(payload)
            return f"Ingreso registrado ({payload['received_at']})"

        note = "devolución del préstamo de Darwin"
        with mock.patch.object(subagent, "_invoke_strict", fake_invoke), \
                mock.patch("agents.subagents.expenses.datetime") as fake_dt:
            fake_dt.now.return_value = SimpleNamespace(date=lambda: TODAY)
            first = tools["create_income_for_user"].invoke(
                {"amount": 200000, "category": "Otros", "received_at": "2026-10-04", "note": note})
            second = tools["create_income_for_user"].invoke(
                {"amount": 200000, "category": "Inversiones", "received_at": None, "note": note})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["received_at"], "2026-10-05")
        self.assertIn("registrado", first)
        self.assertTrue(_blocked(second))
