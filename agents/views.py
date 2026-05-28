import asyncio
import json
import logging
import queue
import threading

from django.http import JsonResponse, StreamingHttpResponse
from langchain_core.messages import AIMessage, HumanMessage
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from .agent_directory import AGENTS, get_agent
from .chat_stream import stream_agent_events, stream_single_agent
from .effective_profile import get_effective_profile
from .models import RiskAssessment, RiskProfile
from .risk_inference import DEFAULT_MAX_AGE_DAYS
from .serializers import (
    RiskAssessmentSerializer,
    RiskProfileSerializer,
    RiskProfileUpdateSerializer,
)

logger = logging.getLogger(__name__)


def _wallbit_connected(user) -> bool:
    """True if the user has a Wallbit account in the connected state."""
    from wallbit.models import WallbitAccount

    return WallbitAccount.objects.filter(
        user=user, status=WallbitAccount.CONNECTED
    ).exists()


class RiskProfileView(APIView):
    """GET/POST/DELETE /api/agents/risk-profile/ — manage the user's risk profile.

    GET returns ``{"exists": false}`` when the user has not been evaluated yet,
    so the frontend can prompt them to run the Risk Profiler. POST upserts the
    profile as a manual override (``user_override=True``) and records a
    ``RiskAssessment`` with ``triggered_by=manual_override`` for audit.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = RiskProfile.objects.filter(user=request.user).first()
        if not profile:
            return Response({"exists": False}, status=status.HTTP_200_OK)
        data = RiskProfileSerializer(profile).data
        return Response({"exists": True, **data}, status=status.HTTP_200_OK)

    def post(self, request):
        profile = RiskProfile.objects.filter(user=request.user).first()
        serializer = RiskProfileUpdateSerializer(
            profile, data=request.data, partial=bool(profile)
        )
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        if profile:
            for field, value in validated.items():
                setattr(profile, field, value)
            profile.user_override = True
            profile.save()
        else:
            profile = RiskProfile.objects.create(
                user=request.user,
                user_override=True,
                **validated,
            )

        RiskAssessment.objects.create(
            profile=profile,
            tolerance=profile.tolerance,
            score=profile.score,
            dimensions=profile.dimensions,
            confidence=profile.confidence,
            reason="Manual override desde el dashboard.",
            triggered_by=RiskAssessment.MANUAL_OVERRIDE,
            context_snapshot={},
        )

        return Response(
            RiskProfileSerializer(profile).data,
            status=status.HTTP_200_OK,
        )

    def delete(self, request):
        deleted, _ = RiskProfile.objects.filter(user=request.user).delete()
        return Response(
            {"deleted": deleted > 0},
            status=status.HTTP_200_OK,
        )


class RiskAssessmentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class RiskAssessmentListView(APIView):
    """GET /api/agents/risk-profile/history/ — paginated assessment history."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = RiskAssessment.objects.filter(profile__user=request.user)
        paginator = RiskAssessmentPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = RiskAssessmentSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class RiskProfileEffectiveView(APIView):
    """GET /api/agents/risk-profile/effective/ — combined declared + inferred profile.

    Query params:
    - ``refresh=1`` forces a fresh auto-inference even if a cached one exists.
    - ``max_age_days=N`` overrides the default 7-day inference cache TTL.

    This is the endpoint frontend and safety guardrails should call. It runs
    on-demand inference (cached for ``max_age_days``) and applies the
    declared-vs-inferred safety rule.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        refresh = request.query_params.get("refresh") in ("1", "true", "yes")
        try:
            max_age = int(request.query_params.get("max_age_days", DEFAULT_MAX_AGE_DAYS))
        except (TypeError, ValueError):
            max_age = DEFAULT_MAX_AGE_DAYS

        effective = get_effective_profile(
            request.user,
            max_age_days=0 if refresh else max_age,
            refresh_inference=True,
        )
        return Response(effective.to_dict(), status=status.HTTP_200_OK)


class ServerSentEventRenderer(BaseRenderer):
    """Lets DRF content negotiation accept ``text/event-stream`` requests.

    The streaming view returns a raw ``StreamingHttpResponse``, so ``render`` is
    never actually invoked — this renderer exists only so the negotiation step in
    ``APIView.initial()`` doesn't reject the client's ``Accept: text/event-stream``.
    """

    media_type = "text/event-stream"
    format = "txt"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


_STREAM_SENTINEL = object()


def _history_to_messages(history) -> list:
    """Convert the client-sent ``[{role, content}]`` list to LangChain messages."""
    messages: list = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if item.get("role") == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


def _iter_agent_sse(user, message: str, history: list, agent_id: str | None = None):
    """Sync generator that bridges the async agent stream to SSE.

    The agents are async; a WSGI worker is sync. We run the stream in a
    background thread (its own event loop) that pushes events onto a queue, and
    this generator drains the queue, yielding ``text/event-stream`` frames as
    they arrive — so the client sees each step live. When ``agent_id`` is given,
    the turn is addressed to that single agent; otherwise it goes to Tresqu.
    """
    events: "queue.Queue" = queue.Queue()

    def _worker():
        async def _pump():
            if agent_id:
                stream = stream_single_agent(user, agent_id, message, "web", history)
            else:
                stream = stream_agent_events(user, message, "web", history)
            async for event in stream:
                events.put(event)

        try:
            asyncio.run(_pump())
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent SSE worker crashed: %s", exc)
            events.put({"type": "error", "text": "Lo siento, hubo un error inesperado."})
        finally:
            events.put(_STREAM_SENTINEL)

    threading.Thread(target=_worker, daemon=True).start()

    # Opening comment flushes headers and defeats some proxy buffering.
    yield ": open\n\n"
    while True:
        event = events.get()
        if event is _STREAM_SENTINEL:
            break
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class AgentChatStreamView(APIView):
    """POST /api/agents/chat/stream/ — run the supervisor and stream its trace.

    Body: ``{"message": str, "history": [{"role": "user"|"assistant", "content": str}]}``.
    Responds with Server-Sent Events: ``step`` (delegations + results), then a
    single ``final`` (text + optional Wallbit ``pending_confirmation``), or ``error``.
    """

    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [ServerSentEventRenderer]

    def post(self, request):
        message = (request.data.get("message") or "").strip()
        if not message:
            return JsonResponse(
                {"detail": "message is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response = StreamingHttpResponse(
            _iter_agent_sse(
                request.user, message, _history_to_messages(request.data.get("history"))
            ),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class AgentRosterView(APIView):
    """GET /api/agents/roster/ — the team of agents available in the web module."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response({"agents": AGENTS}, status=status.HTTP_200_OK)


class AgentDirectChatStreamView(APIView):
    """POST /api/agents/<agent_id>/chat/stream/ — chat directly with one agent.

    Same SSE contract as ``AgentChatStreamView``. For a specialist the stream
    surfaces that agent's own tool calls; ``tresqu`` and ``risk`` reuse the
    supervisor-path routing.
    """

    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [ServerSentEventRenderer]

    def post(self, request, agent_id):
        agent = get_agent(agent_id)
        if agent is None:
            return JsonResponse(
                {"detail": "unknown agent"}, status=status.HTTP_404_NOT_FOUND
            )

        # Agents that need Wallbit are locked behind a connect CTA in the UI;
        # this guards the endpoint too in case it's reached directly.
        if agent.get("requires_wallbit") and not _wallbit_connected(request.user):
            return JsonResponse(
                {
                    "detail": "Conecta tu cuenta Wallbit para hablar con este agente.",
                    "code": "wallbit_not_connected",
                },
                status=status.HTTP_409_CONFLICT,
            )

        message = (request.data.get("message") or "").strip()
        if not message:
            return JsonResponse(
                {"detail": "message is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response = StreamingHttpResponse(
            _iter_agent_sse(
                request.user,
                message,
                _history_to_messages(request.data.get("history")),
                agent_id=agent_id,
            ),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
