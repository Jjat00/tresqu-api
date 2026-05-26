"""Celery tasks para el flujo Composio -> Gmail.

Mantenemos las tasks separadas de la pipeline para que el modulo de
pipeline siga siendo importable sin arrastrar Celery (util para tests
unitarios y scripts de mantenimiento).
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=5,
    retry_backoff=True,
    retry_jitter=True,
)
def process_gmail_message_async(self, processed_email_id: int) -> None:
    """Procesa un ``ProcessedEmail`` con AI parser, crea la transaccion y
    notifica al usuario.

    Disenado para correr en background tras el ack 202 del webhook. Es
    idempotente: si ``ProcessedEmail`` ya esta ``processed`` o
    ``skipped``, no hace nada.
    """
    from django.db import transaction

    from gmailbot.composio_pipeline import process_composio_email
    from gmailbot.models import ProcessedEmail

    try:
        with transaction.atomic():
            try:
                pe = ProcessedEmail.objects.select_for_update().get(
                    id=processed_email_id
                )
            except ProcessedEmail.DoesNotExist:
                logger.warning(
                    "process_gmail_message: ProcessedEmail not found",
                    extra={"processed_email_id": processed_email_id},
                )
                return

            if pe.processing_status in ("processed", "skipped"):
                logger.info(
                    "process_gmail_message: already done",
                    extra={
                        "processed_email_id": processed_email_id,
                        "status": pe.processing_status,
                    },
                )
                return

            process_composio_email(pe)
    except Exception as exc:
        logger.exception(
            "process_gmail_message failed",
            extra={"processed_email_id": processed_email_id},
        )
        # Best-effort: marcar el row como error para visibilidad. Si esto
        # tambien falla, no rompemos el retry de Celery.
        try:
            ProcessedEmail.objects.filter(id=processed_email_id).update(
                processing_status="error",
            )
        except Exception:
            logger.exception(
                "process_gmail_message: failed to mark as error",
                extra={"processed_email_id": processed_email_id},
            )
        # Retry con backoff exponencial gestionado por Celery
        raise self.retry(
            exc=exc,
            countdown=60 * (2 ** self.request.retries),
        )
