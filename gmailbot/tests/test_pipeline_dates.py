"""Punta a punta: un correo de banco cuya fecha la IA lee mal no puede sacar el
gasto del mes real. Se ejecuta ``process_composio_email`` completo con el parser,
el juez de duplicados, los embeddings y WhatsApp parcheados; la BD es real."""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytz
from django.test import TestCase
from django.utils import timezone

from expenses.models import Expense
from gmailbot.models import ComposioConnection, ProcessedEmail
from income.models import Income
from users.models import User


class _FakeEmbeddings:
    def embed_query(self, text):
        return [0.0] * 1536


def _ai_result(**overrides):
    base = {
        "is_purchase": True, "transaction_type": "expense", "payment_status": "success",
        "amount": 3265517.0, "currency": "COP", "merchant": "Bancolombia",
        "date": "2026-02-08", "confidence": 0.95, "suggested_category": None,
        "category_confidence": 0.0, "transaction_reference": None,
    }
    base.update(overrides)
    return base


class GmailPipelineDateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            external_id="573001118888", platform="whatsapp", first_name="Jaime",
            default_currency="COP", timezone="America/Bogota",
        )
        self.conn = ComposioConnection.objects.create(
            user=self.user, status=ComposioConnection.STATUS_ACTIVE,
            connected_account_id="ca_test_dates", google_email="x@gmail.com",
        )
        self.today_local = timezone.now().astimezone(pytz.timezone("America/Bogota")).date()

    def _email(self, message_id, body="Pago por $3.265.517 el 02/08/2026 20:14"):
        pe = ProcessedEmail.objects.create(
            user=self.user, composio_connection=self.conn, gmail_message_id=message_id,
            subject="Alertas y Notificaciones", sender="alertasynotificaciones@bancolombia.com.co",
            processing_status="pending",
        )
        pe.ai_response = json.dumps({"_raw_email": {"subject": pe.subject, "sender": pe.sender,
                                                    "body": body, "thread_id": "t", "date_raw": ""}})
        pe.save(update_fields=["ai_response"])
        return pe

    def _run(self, pe, ai_result):
        from gmailbot import composio_pipeline, email_processor
        with patch.object(composio_pipeline, "parse_purchase_email", return_value=ai_result), \
             patch.object(composio_pipeline, "_judge_duplicate_emails", side_effect=AssertionError("no debería llamarse")), \
             patch.object(email_processor, "embeddings", _FakeEmbeddings()), \
             patch.object(composio_pipeline, "send_transaction_confirmation_whatsapp", return_value="wamid.test"):
            composio_pipeline.process_composio_email(pe)
        pe.refresh_from_db()
        return pe

    def test_dia_y_mes_intercambiados_no_sacan_el_gasto_del_mes(self):
        pe = self._run(self._email("m1"), _ai_result(date="2026-02-08"))
        self.assertEqual(pe.processing_status, "processed", pe.ai_response)
        expense = Expense.objects.get(user=self.user)
        self.assertEqual(expense.amount, Decimal("3265517.00"))
        self.assertEqual(expense.spent_at, self.today_local)

    def test_anio_de_dos_digitos_leido_como_2022(self):
        pe = self._run(self._email("m2", body="Compra por $10.000 el 22/08/26 a las 16:53"),
                       _ai_result(amount=10000, date="2022-08-26"))
        self.assertEqual(pe.processing_status, "processed", pe.ai_response)
        self.assertEqual(Expense.objects.get(user=self.user).spent_at, self.today_local)

    def test_fecha_plausible_se_respeta(self):
        reciente = date.fromordinal(self.today_local.toordinal() - 3)
        pe = self._run(self._email("m3"), _ai_result(date=reciente.isoformat()))
        self.assertEqual(pe.processing_status, "processed", pe.ai_response)
        self.assertEqual(Expense.objects.get(user=self.user).spent_at, reciente)

    def test_ingresos_tambien_protegidos(self):
        pe = self._run(self._email("m4", body="Te transfirieron $40.000 el 01/08/26"),
                       _ai_result(transaction_type="income", amount=40000, merchant="Transferencia", date="2026-01-08"))
        self.assertEqual(pe.processing_status, "processed", pe.ai_response)
        self.assertEqual(Income.objects.get(user=self.user).received_at, self.today_local)
