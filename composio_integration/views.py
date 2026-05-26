"""DRF views for the generic Composio integration endpoints.

All views dispatch by the ``toolkit`` URL kwarg. The webhook and
callback endpoints are intentionally unauthenticated (signature /
signed state token are the only defences); the others rely on the
project-wide JWT default.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.shortcuts import redirect
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .client import get_client
from .exceptions import (
    ComposioError,
    ComposioPermanentError,
    SignatureError,
    StateTokenError,
)
from .registry import get_handler
from .services import ToolkitConnectionService

logger = logging.getLogger(__name__)


class ConnectUrlView(APIView):
    """GET ``/api/integrations/<toolkit>/connect-url/`` — start OAuth flow."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, toolkit: str) -> Response:
        try:
            svc = ToolkitConnectionService()
            dto = svc.start_connect_flow(request.user, toolkit, request=request)
            return Response(
                {
                    "redirect_url": dto.redirect_url,
                    "connected_account_id": dto.connected_account_id,
                    "already_connected": dto.status == "ALREADY_CONNECTED",
                }
            )
        except ComposioPermanentError as exc:
            return Response(
                {"error": str(exc)},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        except ComposioError:
            logger.exception(
                "connect-url failed",
                extra={"toolkit": toolkit, "user_id": request.user.id},
            )
            return Response(
                {"error": "Servicio no disponible"},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class CallbackView(APIView):
    """GET ``/api/integrations/<toolkit>/callback/`` — OAuth landing page.

    Composio appends ``?status=success|failed&connected_account_id=ca_xxx``.
    Success redirects to ``{FRONTEND_URL}/dashboard/profile?<toolkit>=connected``;
    every error path lands on the same profile page with a different query
    flag so the SPA can show a meaningful toast.
    """

    permission_classes: list = []
    authentication_classes: list = []

    def get(self, request: Request, toolkit: str) -> Response:
        state = request.GET.get("state", "")
        status_param = request.GET.get("status", "")
        ca_id = request.GET.get("connected_account_id", "")

        frontend_base = str(settings.FRONTEND_URL).rstrip("/")
        try:
            svc = ToolkitConnectionService()
            svc.complete_connect_flow(state, status_param, ca_id)
            return redirect(f"{frontend_base}/dashboard/profile?{toolkit}=connected")
        except StateTokenError:
            logger.warning(
                "callback state invalid",
                extra={"toolkit": toolkit, "event": "callback_state_invalid"},
            )
            return redirect(f"{frontend_base}/dashboard/profile?{toolkit}=invalid_state")
        except ComposioPermanentError as exc:
            logger.warning(
                "callback failed",
                extra={
                    "toolkit": toolkit,
                    "event": "callback_failed",
                    "reason": str(exc),
                },
            )
            return redirect(f"{frontend_base}/dashboard/profile?{toolkit}=failed")
        except ComposioError:
            logger.exception(
                "callback transient error",
                extra={"toolkit": toolkit},
            )
            return redirect(f"{frontend_base}/dashboard/profile?{toolkit}=error")


class WebhookView(APIView):
    """POST ``/api/integrations/<toolkit>/composio-webhook/`` — trigger events.

    Signature is the only authentication; JWT/CSRF are explicitly off.
    Design choice: if the connection is disconnected/failed we still return
    200 so the upstream stops retrying, but we drop the payload and log it.
    """

    permission_classes: list = []
    authentication_classes: list = []

    def post(self, request: Request, toolkit: str) -> Response:
        # Late import: avoids loading gmailbot models at module import time.
        from gmailbot.models import ComposioConnection

        try:
            parsed = get_client().verify_webhook(dict(request.headers), request.body)
        except SignatureError:
            return Response(status=http_status.HTTP_401_UNAUTHORIZED)

        ca_id = parsed.metadata.get("connected_account_id", "")
        if not ca_id:
            logger.warning(
                "webhook missing connected_account_id",
                extra={
                    "toolkit": toolkit,
                    "event": "webhook_missing_account",
                },
            )
            return Response(status=http_status.HTTP_400_BAD_REQUEST)

        connection = ComposioConnection.objects.filter(
            connected_account_id=ca_id
        ).first()
        if connection is None:
            logger.warning(
                "webhook for unknown connection",
                extra={
                    "toolkit": toolkit,
                    "connected_account_id": ca_id,
                    "event": "webhook_unknown_connection",
                },
            )
            return Response(status=http_status.HTTP_200_OK)

        if connection.status in (
            ComposioConnection.STATUS_DISCONNECTED,
            ComposioConnection.STATUS_FAILED,
        ):
            logger.warning(
                "webhook for inactive connection dropped",
                extra={
                    "toolkit": toolkit,
                    "user_id": connection.user_id,
                    "connected_account_id": ca_id,
                    "status": connection.status,
                    "event": "webhook_dropped_inactive",
                },
            )
            return Response(status=http_status.HTTP_200_OK)

        try:
            handler = get_handler(toolkit)
        except KeyError:
            logger.error(
                "webhook for unregistered toolkit",
                extra={
                    "toolkit": toolkit,
                    "event": "webhook_no_handler",
                },
            )
            return Response(status=http_status.HTTP_404_NOT_FOUND)

        try:
            handler.handle_webhook(parsed, connection)
        except Exception:
            # The handler is expected to persist the idempotency row before
            # any failure; the stale sweep will re-enqueue orphans.
            logger.exception(
                "webhook handler raised",
                extra={
                    "toolkit": toolkit,
                    "connected_account_id": ca_id,
                },
            )

        return Response(status=http_status.HTTP_202_ACCEPTED)


class DisconnectView(APIView):
    """POST ``/api/integrations/<toolkit>/disconnect/`` — user-initiated drop."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, toolkit: str) -> Response:
        try:
            ToolkitConnectionService().disconnect(request.user, toolkit)
            return Response(status=http_status.HTTP_204_NO_CONTENT)
        except ComposioError:
            logger.exception(
                "disconnect failed",
                extra={"toolkit": toolkit, "user_id": request.user.id},
            )
            return Response(
                {"error": "No se pudo desconectar"},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class StatusView(APIView):
    """GET ``/api/integrations/<toolkit>/status/`` — connection state for the dashboard."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, toolkit: str) -> Response:
        from gmailbot.models import ComposioConnection, ProcessedEmail

        connection = ComposioConnection.objects.filter(user=request.user).first()
        if connection is None:
            return Response(
                {
                    "connected": False,
                    "status": "none",
                    "google_email": None,
                    "connected_at": None,
                    "trigger_active": False,
                    "total_emails_processed": 0,
                    "total_purchases_detected": 0,
                    "pending_categorization": 0,
                }
            )

        emails_qs = ProcessedEmail.objects.filter(user=request.user)
        return Response(
            {
                "connected": connection.status == ComposioConnection.STATUS_ACTIVE,
                "status": connection.status,
                "google_email": connection.google_email or None,
                "connected_at": connection.created_at,
                "trigger_active": bool(connection.trigger_id),
                "last_error": connection.last_error or None,
                "total_emails_processed": emails_qs.filter(
                    processing_status="processed"
                ).count(),
                "total_purchases_detected": emails_qs.filter(is_purchase=True).count(),
                "pending_categorization": emails_qs.filter(
                    awaiting_categorization=True
                ).count(),
            }
        )


class RetryTriggerView(APIView):
    """POST ``/api/integrations/<toolkit>/retry-trigger/`` — re-arm provisioning."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, toolkit: str) -> Response:
        from gmailbot.models import ComposioConnection

        connection = ComposioConnection.objects.filter(user=request.user).first()
        if connection is None or connection.status != ComposioConnection.STATUS_ACTIVE:
            return Response(
                {"error": "Conexión no activa"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        from .tasks import provision_triggers_async

        provision_triggers_async.delay(connection.id, toolkit)
        return Response(status=http_status.HTTP_202_ACCEPTED)
