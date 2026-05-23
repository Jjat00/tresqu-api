import logging

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .client import (
    WallbitAuthError,
    WallbitClient,
    WallbitError,
    WallbitPermissionError,
)
from .crypto import encrypt_api_key
from .models import WallbitAccount
from .serializers import WallbitConnectSerializer, WallbitStatusSerializer

logger = logging.getLogger(__name__)


class WallbitConnectView(APIView):
    """POST /api/wallbit/connect — validate and persist a Wallbit API key.

    The key is verified against /balance/checking before being encrypted
    and stored. The plaintext key never leaves this view.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = WallbitConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        api_key = serializer.validated_data["api_key"]
        scope_hint = serializer.validated_data.get("scope_hint") or "read,trade"

        try:
            with WallbitClient(api_key) as client:
                client.get("/balance/checking")
        except WallbitAuthError:
            return Response(
                {"detail": "API key inválida o sin permisos."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except WallbitPermissionError:
            return Response(
                {"detail": "La API key no tiene permisos suficientes (scope o IP whitelist)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        except WallbitError as exc:
            logger.warning("wallbit connect validation failed", exc_info=exc)
            return Response(
                {"detail": f"No se pudo validar la API key: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        account, _ = WallbitAccount.objects.update_or_create(
            user=request.user,
            defaults={
                "encrypted_api_key": encrypt_api_key(api_key),
                "scope_hint": scope_hint,
                "status": WallbitAccount.CONNECTED,
                "last_error": "",
                "kill_switch_until": None,
            },
        )
        return Response(WallbitStatusSerializer(account).data, status=status.HTTP_200_OK)


class WallbitStatusView(APIView):
    """GET /api/wallbit/status — connection state for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        account = WallbitAccount.objects.filter(user=request.user).first()
        if not account:
            return Response({"connected": False}, status=status.HTTP_200_OK)
        return Response(WallbitStatusSerializer(account).data, status=status.HTTP_200_OK)


class WallbitDisconnectView(APIView):
    """POST /api/wallbit/disconnect — revoke locally (does not call DELETE /api-key)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        account = WallbitAccount.objects.filter(user=request.user).first()
        if not account:
            return Response({"connected": False}, status=status.HTTP_200_OK)
        account.status = WallbitAccount.REVOKED
        account.kill_switch_until = timezone.now() + timezone.timedelta(days=365)
        account.save(update_fields=["status", "kill_switch_until"])
        return Response(WallbitStatusSerializer(account).data, status=status.HTTP_200_OK)
