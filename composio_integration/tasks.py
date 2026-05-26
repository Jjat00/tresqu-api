"""Celery tasks for the Composio integration layer.

The retry policy targets only ``ComposioTransientError`` so we never
burn retries on 4xx errors that won't recover.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .exceptions import ComposioPermanentError, ComposioTransientError

logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(ComposioTransientError,),
    max_retries=5,
    retry_backoff=True,
    retry_jitter=True,
)
def provision_triggers_async(connection_id: int, toolkit_slug: str) -> None:
    """Provision the triggers declared by the toolkit handler."""
    from gmailbot.models import ComposioConnection

    from .services import ToolkitConnectionService

    try:
        connection = ComposioConnection.objects.get(id=connection_id)
    except ComposioConnection.DoesNotExist:
        logger.warning(
            "provision: connection not found",
            extra={"connection_id": connection_id, "toolkit": toolkit_slug},
        )
        return

    if connection.status != ComposioConnection.STATUS_ACTIVE:
        logger.warning(
            "provision: connection not active",
            extra={
                "connection_id": connection_id,
                "status": connection.status,
                "toolkit": toolkit_slug,
            },
        )
        return

    try:
        ToolkitConnectionService().provision_triggers(connection)
    except ComposioPermanentError as exc:
        # Permanent: stop retrying, mark the row so the retry sweep
        # picks it up on the slow path (hourly).
        connection.status = ComposioConnection.STATUS_FAILED
        connection.last_error = f"trigger provisioning failed: {exc}"
        connection.save(
            update_fields=["status", "last_error", "updated_at"]
        )
        logger.error(
            "provision failed permanent",
            extra={
                "connection_id": connection_id,
                "toolkit": toolkit_slug,
                "error": str(exc),
            },
        )


@shared_task
def reprocess_stale_pending() -> None:
    """Beat: re-enqueue ``ProcessedEmail`` rows stuck in pending > 10 min."""
    from gmailbot.models import ProcessedEmail

    cutoff = timezone.now() - timedelta(minutes=10)
    stale_qs = ProcessedEmail.objects.filter(
        processing_status="pending", updated_at__lt=cutoff
    ).only("id")

    # Late import so the generic layer does not hard-depend on gmailbot.
    try:
        from gmailbot.composio_tasks import process_gmail_message_async
    except ImportError:
        logger.warning(
            "reprocess_stale_pending: gmailbot tasks not available",
            extra={"event": "stale_pending_no_consumer"},
        )
        return

    count = 0
    for pe in stale_qs.iterator(chunk_size=100):
        process_gmail_message_async.delay(pe.id)
        count += 1

    if count:
        logger.info(
            "reprocess_stale_pending re-enqueued",
            extra={"count": count, "event": "stale_pending_reenqueued"},
        )


@shared_task(
    autoretry_for=(ComposioTransientError,),
    max_retries=5,
    retry_backoff=True,
    retry_jitter=True,
)
def retry_failed_connections() -> None:
    """Beat: retry trigger provisioning on ``failed`` connections."""
    from gmailbot.models import ComposioConnection

    qs = ComposioConnection.objects.filter(
        status=ComposioConnection.STATUS_FAILED,
        last_error__icontains="trigger provisioning failed",
    )
    for conn in qs.iterator(chunk_size=50):
        # Flip back to active so ``provision_triggers`` re-runs its checks.
        conn.status = ComposioConnection.STATUS_ACTIVE
        conn.last_error = ""
        conn.save(update_fields=["status", "last_error", "updated_at"])
        # TODO: confirmar con plan — toolkit_slug fijado a 'gmail' hasta
        # que el modelo ComposioConnection tenga columna ``toolkit``.
        provision_triggers_async.delay(conn.id, "gmail")
