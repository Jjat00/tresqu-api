"""Handler del toolkit Gmail registrado en composio_integration.

Recibe webhooks ya verificados con firma HMAC valida; su job es:

1. extraer datos del email del payload,
2. crear ``ProcessedEmail`` idempotente (dedupe por
   ``(user, composio_connection, gmail_message_id)``),
3. encolar la Celery task de procesamiento AI.

El protocolo ``ToolkitHandler`` esta definido en
``composio_integration.registry`` y la app ``composio_integration`` lo
invoca tras validar la firma. Nunca expongas el termino "Composio" en
mensajes destinados al usuario final.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from composio_integration.client import ParsedPayload

logger = logging.getLogger(__name__)


class GmailComposioHandler:
    """Implementa el protocolo ``ToolkitHandler`` para Gmail."""

    slug: str = "gmail"
    trigger_specs: list[dict] = [
        {"slug": "GMAIL_NEW_GMAIL_MESSAGE", "config": {}},
    ]

    def handle_webhook(self, parsed: ParsedPayload, connection: Any) -> None:
        """Webhook entrante de Gmail. Crea ``ProcessedEmail`` y encola la task.

        El payload V3 del trigger ``GMAIL_NEW_GMAIL_MESSAGE`` no es
        completamente estable entre versiones del SDK: probamos varias
        claves comunes para extraer el ``message_id`` y el cuerpo.
        """
        from django.db import IntegrityError, transaction

        from gmailbot.composio_tasks import process_gmail_message_async
        from gmailbot.models import ProcessedEmail

        data: dict[str, Any] = parsed.data or {}

        gmail_message_id = (
            data.get("id")
            or data.get("message_id")
            or data.get("gmail_message_id")
            or ""
        )
        if not gmail_message_id:
            logger.warning(
                "gmail webhook missing message id",
                extra={
                    "event": "gmail_webhook_missing_id",
                    "user_id": getattr(connection, "user_id", None),
                },
            )
            return

        subject = (data.get("subject") or "")[:500]
        sender = (
            data.get("from")
            or data.get("sender")
            or data.get("from_email")
            or ""
        )[:255]

        try:
            with transaction.atomic():
                processed_email, created = ProcessedEmail.objects.get_or_create(
                    user=connection.user,
                    composio_connection=connection,
                    gmail_message_id=gmail_message_id,
                    defaults={
                        "subject": subject,
                        "sender": sender,
                        "processing_status": "pending",
                    },
                )
        except IntegrityError:
            # Race condition: otro worker creo la fila entre el SELECT y el
            # INSERT del get_or_create. Recuperamos la existente.
            processed_email = ProcessedEmail.objects.filter(
                user=connection.user,
                composio_connection=connection,
                gmail_message_id=gmail_message_id,
            ).first()
            created = False

        if processed_email is None:
            logger.error(
                "gmail webhook ProcessedEmail create failed",
                extra={
                    "event": "gmail_webhook_create_failed",
                    "user_id": getattr(connection, "user_id", None),
                    "gmail_message_id": gmail_message_id,
                },
            )
            return

        if not created and processed_email.processing_status in (
            "processed",
            "skipped",
            "duplicate",
        ):
            logger.info(
                "gmail webhook duplicate ignored",
                extra={
                    "event": "gmail_webhook_duplicate",
                    "user_id": getattr(connection, "user_id", None),
                    "gmail_message_id": gmail_message_id,
                    "status": processed_email.processing_status,
                },
            )
            return

        # Persistimos el email crudo en ai_response para que la Celery task
        # lo recupere sin re-leer el webhook. Aun no existe un campo
        # dedicado ``raw_data``; reutilizamos ai_response (TextField) con un
        # prefijo ``_raw_email`` que la pipeline reconoce.
        body = (
            data.get("message_text")
            or data.get("snippet")
            or data.get("body")
            or ""
        )
        thread_id = data.get("thread_id") or ""
        date_raw = data.get("date") or data.get("timestamp") or ""

        processed_email.ai_response = json.dumps(
            {
                "_raw_email": {
                    "subject": subject,
                    "sender": sender,
                    "body": body,
                    "thread_id": thread_id,
                    "date_raw": date_raw,
                }
            },
            ensure_ascii=False,
        )
        processed_email.save(update_fields=["ai_response", "updated_at"])

        process_gmail_message_async.delay(processed_email.id)
        logger.info(
            "gmail webhook enqueued",
            extra={
                "event": "gmail_webhook_enqueued",
                "user_id": getattr(connection, "user_id", None),
                "gmail_message_id": gmail_message_id,
                "processed_email_id": processed_email.id,
            },
        )

    def on_connected(self, connection: Any) -> None:
        """Hook tras conexion exitosa. El frontend ya redirige con flag."""
        logger.info(
            "gmail handler on_connected",
            extra={
                "event": "gmail_connected",
                "user_id": getattr(connection, "user_id", None),
                "connected_account_id": getattr(
                    connection, "connected_account_id", ""
                ),
            },
        )

    def on_disconnected(self, connection: Any) -> None:
        """Hook tras desconexion / revocacion."""
        logger.info(
            "gmail handler on_disconnected",
            extra={
                "event": "gmail_disconnected",
                "user_id": getattr(connection, "user_id", None),
                "connected_account_id": getattr(
                    connection, "connected_account_id", ""
                ),
            },
        )
