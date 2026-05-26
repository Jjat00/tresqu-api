"""Signals para limpieza upstream en Composio cuando se borra un usuario.

Cumple GDPR Art. 17: si borramos el ``User``, también revocamos el
trigger y la cuenta conectada en Composio para que no sigan polleando
emails de un usuario que ya no existe.

Las llamadas son best-effort: errores se loguean pero no propagan,
porque bloquear el borrado del usuario por un fallo de Composio sería
peor que dejar un trigger huérfano que igual fallará en la próxima
ejecución del usuario inexistente.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import pre_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)

User = get_user_model()


@receiver(pre_delete, sender=User)
def cleanup_composio_on_user_delete(sender, instance, **kwargs) -> None:
    """Antes de borrar el User: revocar trigger + connected_account en Composio."""
    try:
        connection = getattr(instance, 'composio_connection', None)
    except Exception:
        connection = None
    if connection is None:
        return

    trigger_id = getattr(connection, 'trigger_id', '') or ''
    connected_account_id = getattr(connection, 'connected_account_id', '') or ''

    try:
        from composio_integration.client import get_client
        from composio_integration.exceptions import ComposioError
    except ImportError:
        logger.warning(
            "composio cleanup skipped: SDK wrapper unavailable",
            extra={"event": "composio_cleanup_skipped", "user_id": instance.id},
        )
        return

    try:
        client = get_client()
    except Exception:
        logger.exception(
            "composio cleanup: client unavailable",
            extra={"event": "composio_cleanup_no_client", "user_id": instance.id},
        )
        return

    if trigger_id:
        try:
            client.delete_trigger(trigger_id)
            logger.info(
                "composio trigger deleted on user delete",
                extra={
                    "event": "composio_trigger_deleted",
                    "user_id": instance.id,
                    "trigger_id": trigger_id,
                },
            )
        except ComposioError:
            logger.warning(
                "composio trigger delete failed (best-effort)",
                extra={
                    "event": "composio_trigger_delete_failed",
                    "user_id": instance.id,
                    "trigger_id": trigger_id,
                },
            )

    if connected_account_id:
        try:
            client.delete_connected_account(connected_account_id)
            logger.info(
                "composio account deleted on user delete",
                extra={
                    "event": "composio_account_deleted",
                    "user_id": instance.id,
                    "connected_account_id": connected_account_id,
                },
            )
        except ComposioError:
            logger.warning(
                "composio account delete failed (best-effort)",
                extra={
                    "event": "composio_account_delete_failed",
                    "user_id": instance.id,
                    "connected_account_id": connected_account_id,
                },
            )
