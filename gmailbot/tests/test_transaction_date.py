from datetime import date, timedelta

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

    def test_bordes_de_la_ventana(self):
        limite = self.today - timedelta(days=45)
        self.assertEqual(resolve_transaction_date(limite.isoformat(), self.today), (limite, None))
        self.assertEqual(resolve_transaction_date((limite - timedelta(days=1)).isoformat(), self.today), (self.today, "too_old"))
        self.assertEqual(resolve_transaction_date((self.today + timedelta(days=1)).isoformat(), self.today), (self.today, "future"))

    def test_nunca_devuelve_una_fecha_fuera_de_la_ventana(self):
        """Fuzz: para cualquier fecha extraída (±3 años) el resultado cae en
        [hoy-45, hoy]. Es la garantía que necesita el dashboard mensual."""
        for offset in range(-1100, 1100):
            extraida = self.today + timedelta(days=offset)
            got, _ = resolve_transaction_date(extraida.isoformat(), self.today)
            self.assertLessEqual(got, self.today, extraida)
            self.assertGreaterEqual(got, self.today - timedelta(days=45), extraida)

    def test_formatos_raros_no_rompen(self):
        for raw in ["22/08/26", "08/22/2026", "ayer", 20260822, "2026-13-01", "2026-02-30"]:
            got, reason = resolve_transaction_date(raw, self.today)
            self.assertEqual(got, self.today, raw)
            self.assertEqual(reason, "unparseable", raw)
