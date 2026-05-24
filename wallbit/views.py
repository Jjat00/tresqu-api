import logging

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .agent_safety import AccountNotConnected, get_account_or_raise, get_pending_decision
from .client import (
    WallbitAuthError,
    WallbitClient,
    WallbitError,
    WallbitPermissionError,
)
from .crypto import encrypt_api_key
from .executors import UnknownTool, execute_decision
from .models import AgentDecision, AgentLimits, Investment, WallbitAccount
from .portfolio import get_holdings, get_summary, get_timeline
from .serializers import (
    AgentDecisionSerializer,
    AgentLimitsSerializer,
    HoldingSerializer,
    InvestmentSerializer,
    PortfolioSummarySerializer,
    TimelinePointSerializer,
    WallbitConnectSerializer,
    WallbitStatusSerializer,
)

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


class AgentDecisionPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class AgentDecisionListView(APIView):
    """GET /api/wallbit/agent/decisions — paginated audit log for the user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = AgentDecision.objects.filter(user=request.user).order_by("-created_at")
        paginator = AgentDecisionPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = AgentDecisionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AgentConfirmView(APIView):
    """POST /api/wallbit/agent/confirm/{decision_id} — execute a pending decision."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, decision_id: int):
        try:
            decision = get_pending_decision(request.user, decision_id)
        except AgentDecision.DoesNotExist:
            return Response(
                {"detail": "Decision not found or already resolved."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            account = get_account_or_raise(request.user)
        except Exception as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        if account.kill_switch_until and account.kill_switch_until > timezone.now():
            return Response(
                {"detail": "Kill switch active — cannot execute."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = execute_decision(decision, account)
        except UnknownTool as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        decision.refresh_from_db()
        return Response(
            {
                "result": result,
                "decision": AgentDecisionSerializer(decision).data,
            },
            status=status.HTTP_200_OK if result.get("ok") else status.HTTP_502_BAD_GATEWAY,
        )


class AgentLimitsView(APIView):
    """GET/POST /api/wallbit/limits — read or update the user's AgentLimits."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        limits, _ = AgentLimits.objects.get_or_create(user=request.user)
        return Response(AgentLimitsSerializer(limits).data)

    def post(self, request):
        limits, _ = AgentLimits.objects.get_or_create(user=request.user)
        serializer = AgentLimitsSerializer(limits, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class WallbitSyncView(APIView):
    """POST /api/wallbit/sync — manually trigger a transaction sync."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            account = get_account_or_raise(request.user)
        except Exception as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        from .tasks import sync_wallbit_transactions

        async_result = sync_wallbit_transactions.delay(account.id)
        return Response(
            {"task_id": async_result.id, "queued": True},
            status=status.HTTP_202_ACCEPTED,
        )


class InvestmentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class InvestmentListView(APIView):
    """GET /api/wallbit/investments — paginated list of user's investments.

    Query params: ``kind`` (STOCK|ETF|BOND|ROBO|CHEST),
    ``action`` (BUY|SELL|DEPOSIT|WITHDRAW), ``symbol``.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Investment.objects.filter(user=request.user).order_by("-created_at")
        kind = request.query_params.get("kind")
        action = request.query_params.get("action")
        symbol = request.query_params.get("symbol")
        if kind:
            qs = qs.filter(kind=kind.upper())
        if action:
            qs = qs.filter(action=action.upper())
        if symbol:
            qs = qs.filter(symbol__iexact=symbol)

        paginator = InvestmentPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = InvestmentSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class PortfolioSummaryView(APIView):
    """GET /api/wallbit/portfolio/summary — hero metrics with live valuation."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            summary = get_summary(request.user)
        except AccountNotConnected:
            return Response(
                {"detail": "Wallbit not connected", "connected": False},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )
        except WallbitError as exc:
            logger.warning("portfolio summary upstream failure", exc_info=exc)
            return Response(
                {"detail": f"Wallbit upstream error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(PortfolioSummarySerializer(summary).data)


class PortfolioHoldingsView(APIView):
    """GET /api/wallbit/portfolio/holdings — live positions with cost basis + P&L."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            holdings = get_holdings(request.user)
        except AccountNotConnected:
            return Response(
                {"detail": "Wallbit not connected", "connected": False},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )
        except WallbitError as exc:
            logger.warning("portfolio holdings upstream failure", exc_info=exc)
            return Response(
                {"detail": f"Wallbit upstream error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(HoldingSerializer(holdings, many=True).data)


class PortfolioTimelineView(APIView):
    """GET /api/wallbit/portfolio/timeline — cumulative net invested over time."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "3m").lower()
        points = get_timeline(request.user, period=period)
        return Response(
            {
                "period": period,
                "points": TimelinePointSerializer(points, many=True).data,
            }
        )
