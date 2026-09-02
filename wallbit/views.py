import logging

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .agent_safety import (
    AccountNotConnected,
    claim_pending_decision,
    get_account_or_raise,
    get_pending_decision,
    mark_failed,
)
from .confirmation_actions import cancel_pending_decision
from .client import (
    WallbitAuthError,
    WallbitClient,
    WallbitError,
    WallbitPermissionError,
)
from .crypto import encrypt_api_key
from .executors import LOCAL_ONLY_TOOLS, UnknownTool, execute_decision
from .models import AgentDecision, AgentLimits, Investment, WallbitAccount
from .portfolio import (
    WallbitUnavailableError,
    get_asset_catalog,
    get_holdings,
    get_pnl_timeline,
    get_summary,
    get_timeline,
    invalidate_snapshot,
)
from .serializers import (
    AgentDecisionSerializer,
    AgentLimitsSerializer,
    HoldingSerializer,
    InvestmentSerializer,
    PnLTimelinePointSerializer,
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
        # A (re)connected key must not serve the previous key's cached snapshot.
        invalidate_snapshot(account.id)
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


# Bounded set of pause durations the dashboard offers. Custom values are
# accepted via the API (capped at 30 days) but the UI sticks to these
# presets so users don't accidentally lock themselves out for a year.
_PAUSE_PRESETS_HOURS = {1, 6, 24, 72, 168}
_PAUSE_MAX_HOURS = 24 * 30


class WallbitPauseView(APIView):
    """POST /api/wallbit/pause — activate the agent kill switch for N hours.

    Safety-positive action: pausing the bot only adds protection. The
    request body accepts ``{"duration_hours": int}`` (default 24). If a
    pause is already active and the new ``until`` would be sooner, we
    keep the longer of the two — never weaken an existing pause.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        account = WallbitAccount.objects.filter(user=request.user).first()
        if not account:
            return Response(
                {"detail": "No Wallbit account connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_hours = request.data.get("duration_hours", 24)
        try:
            hours = int(raw_hours)
        except (TypeError, ValueError):
            return Response(
                {"detail": "duration_hours must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if hours <= 0 or hours > _PAUSE_MAX_HOURS:
            return Response(
                {"detail": f"duration_hours must be between 1 and {_PAUSE_MAX_HOURS}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_until = timezone.now() + timezone.timedelta(hours=hours)
        if account.kill_switch_until and account.kill_switch_until > new_until:
            # Don't shorten an existing protection — keep the longer pause.
            pass
        else:
            account.kill_switch_until = new_until
            account.save(update_fields=["kill_switch_until"])

        logger.info(
            "wallbit pause: user=%s duration_hours=%s until=%s",
            request.user.id, hours, account.kill_switch_until,
        )
        return Response(WallbitStatusSerializer(account).data, status=status.HTTP_200_OK)


class WallbitResumeView(APIView):
    """POST /api/wallbit/resume — clear the kill switch.

    Safety-negative action: removing protection. Requires the user to be
    authenticated. The agent layer requires an extra confirmation step
    before exposing this (see ``wallbit/write_tools.py``); the REST
    endpoint trusts the JWT'd user directly.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        account = WallbitAccount.objects.filter(user=request.user).first()
        if not account:
            return Response(
                {"detail": "No Wallbit account connected."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if account.status == WallbitAccount.REVOKED:
            return Response(
                {"detail": "Account is revoked — reconnect from the dashboard."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        account.kill_switch_until = None
        account.save(update_fields=["kill_switch_until"])
        logger.info("wallbit resume: user=%s cleared kill switch", request.user.id)
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
        # Row-locked claim: a concurrent confirm of the same decision gets 404
        # here and never reaches Wallbit (see agent_safety.claim_pending_decision).
        try:
            decision = claim_pending_decision(request.user, decision_id)
        except AgentDecision.DoesNotExist:
            return Response(
                {"detail": "Decision not found, in progress or already resolved."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            account = get_account_or_raise(request.user)
        except Exception as exc:
            mark_failed(decision, error=str(exc))
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        tool_name = (decision.tools_called or [{}])[0].get("tool", "")
        bypass_kill_switch = tool_name in LOCAL_ONLY_TOOLS
        if (
            not bypass_kill_switch
            and account.kill_switch_until
            and account.kill_switch_until > timezone.now()
        ):
            mark_failed(decision, error="kill_switch_active")
            return Response(
                {"detail": "Kill switch active — cannot execute."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            result = execute_decision(decision, account)
        except UnknownTool as exc:
            mark_failed(decision, error=str(exc))
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        decision.refresh_from_db()
        if result.get("ok"):
            http_status = status.HTTP_200_OK
        elif result.get("uncertain"):
            # Wallbit gave no answer: the order may have filled. Not an error
            # and not a success — the decision is being reconciled.
            http_status = status.HTTP_202_ACCEPTED
        else:
            http_status = status.HTTP_502_BAD_GATEWAY
        return Response(
            {
                "result": result,
                "decision": AgentDecisionSerializer(decision).data,
            },
            status=http_status,
        )


class AgentCancelView(APIView):
    """POST /api/wallbit/agent/cancel/{decision_id} — cancel a pending decision."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, decision_id: int):
        try:
            get_pending_decision(request.user, decision_id)
        except AgentDecision.DoesNotExist:
            return Response(
                {"detail": "Decision not found or already resolved."},
                status=status.HTTP_404_NOT_FOUND,
            )
        message = cancel_pending_decision(request.user, decision_id)
        return Response({"detail": message}, status=status.HTTP_200_OK)


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
    """POST /api/wallbit/sync — manually trigger a transaction sync.

    Default mode runs the sync inline so the dashboard can refresh with
    fresh data on the same request. Pass ``?async=true`` to keep the old
    fire-and-forget behaviour (queues via Celery and returns ``task_id``).
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            account = get_account_or_raise(request.user)
        except Exception as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        from .tasks import run_wallbit_sync, sync_wallbit_transactions

        wants_async = request.query_params.get("async", "").lower() in ("1", "true", "yes")
        if wants_async:
            async_result = sync_wallbit_transactions.delay(account.id)
            return Response(
                {"task_id": async_result.id, "queued": True},
                status=status.HTTP_202_ACCEPTED,
            )

        result = run_wallbit_sync(account.id)
        account.refresh_from_db()
        payload = {
            **result,
            "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None,
        }
        http_status = status.HTTP_200_OK if result.get("ok") else status.HTTP_502_BAD_GATEWAY
        return Response(payload, status=http_status)


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
        except WallbitUnavailableError as exc:
            # Wallbit is down / rate-limiting us and there is no last-good
            # snapshot yet: say so instead of rendering a $0 portfolio.
            return Response(
                {"detail": str(exc), "unavailable": True},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
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
        except WallbitUnavailableError as exc:
            return Response(
                {"detail": str(exc), "unavailable": True},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
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


class PortfolioPnLTimelineView(APIView):
    """GET /api/wallbit/portfolio/pnl-timeline — gains/losses (P&L) over time.

    Periods: 1w, 1m, 1y, ytd, all. The last point is anchored to the live
    summary so it matches the hero P&L. ``stale`` flags an approximated series
    (missing price history or legacy rows without precise share counts).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        period = request.query_params.get("period", "1m").lower()
        try:
            points, stale = get_pnl_timeline(request.user, period=period)
        except AccountNotConnected:
            return Response(
                {"detail": "Wallbit not connected", "connected": False},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )
        return Response(
            {
                "period": period,
                "points": PnLTimelinePointSerializer(points, many=True).data,
                "stale": stale,
            }
        )


def _normalize_asset(a: dict) -> dict:
    return {
        "symbol": (a.get("symbol") or "").upper(),
        "name": a.get("name") or a.get("description") or "",
        "asset_type": a.get("asset_type") or a.get("type") or "",
        "sector": a.get("sector") or "",
        "price": a.get("price"),
        "logo_url": a.get("logo_url") or "",
        "country": a.get("country") or "",
    }


class AssetSearchView(APIView):
    """GET /api/wallbit/assets/search — browse the Wallbit asset catalog.

    Lets the user explore any stock/ETF (not just what they hold) to then view
    its price chart. Requires a connected Wallbit account (the catalog is read
    with the user's key).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        category = request.query_params.get("category", "").strip().upper()
        try:
            limit = int(request.query_params.get("limit", 12))
        except (TypeError, ValueError):
            limit = 12
        limit = max(1, min(limit, 50))

        try:
            account = get_account_or_raise(request.user)
            items, stale = get_asset_catalog(
                account, query=query, category=category, limit=limit
            )
        except AccountNotConnected:
            return Response(
                {"detail": "Wallbit not connected", "connected": False},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )
        except WallbitError as exc:
            logger.warning("asset search upstream failure", exc_info=exc)
            return Response(
                {"detail": f"Wallbit upstream error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        assets = [_normalize_asset(a) for a in items]
        return Response({"assets": assets, "stale": stale})
