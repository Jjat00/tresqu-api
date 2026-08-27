from datetime import date

from django.test import SimpleTestCase

from gmailbot.composio_pipeline import resolve_transaction_date


class ResolveTransactionDateTests(SimpleTestCase):
    """Red de seguridad para la fecha que extrae la IA de un correo bancario.

    Casos reales (usuario 7, agosto 2026): "22/08/26" leído como 2022-08-26 y
    "02/08/2026" leído como 2026-02-08 dejaban el gasto fuera del mes y el
    agente y el dashboard descuadraban por 3,6M COP."""

    today = date(2026, 8, 22)

    def test_fecha_reciente_se_acepta(self):
        self.assertEqual(resolve_transaction_date("2026-08-20", self.today), (date(2026, 8, 20), None))

    def test_mismo_dia_se_acepta(self):
        self.assertEqual(resolve_transaction_date("2026-08-22", self.today), (date(2026, 8, 22), None))

    def test_anio_de_dos_digitos_leido_como_2022_cae_a_hoy(self):
        self.assertEqual(resolve_transaction_date("2022-08-26", self.today), (self.today, "too_old"))

    def test_dia_y_mes_intercambiados_caen_a_hoy(self):
        self.assertEqual(resolve_transaction_date("2026-02-08", date(2026, 8, 2)), (date(2026, 8, 2), "too_old"))

    def test_fecha_futura_cae_a_hoy(self):
        self.assertEqual(resolve_transaction_date("2026-09-01", self.today), (self.today, "future"))

    def test_sin_fecha_o_basura_cae_a_hoy(self):
        self.assertEqual(resolve_transaction_date(None, self.today), (self.today, None))
        self.assertEqual(resolve_transaction_date("", self.today), (self.today, None))
        self.assertEqual(resolve_transaction_date("22/08/26", self.today), (self.today, "unparseable"))

    def test_recibo_de_hace_un_mes_se_acepta(self):
        self.assertEqual(resolve_transaction_date("2026-07-25", self.today), (date(2026, 7, 25), None))
