import asyncio
import json
import logging
import queue
import threading

from django.http import StreamingHttpResponse
from langchain_core.messages import AIMessage, HumanMessage
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .chat_stream import stream_agent_events
from .effective_profile import get_effective_profile
from .models import RiskAssessment, RiskProfile
from .risk_inference import DEFAULT_MAX_AGE_DAYS
from .serializers import (
    RiskAssessmentSerializer,
    RiskProfileSerializer,
    RiskProfileUpdateSerializer,
)

logger = logging.getLogger(__name__)


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


def _iter_agent_sse(user, message: str, history: list):
    """Sync generator that bridges the async supervisor stream to SSE.

    The supervisor is async; a WSGI worker is sync. We run ``stream_agent_events``
    in a background thread (its own event loop) that pushes events onto a queue,
    and this generator drains the queue, yielding ``text/event-stream`` frames as
    they arrive — so the client sees each agent step live.
    """
    events: "queue.Queue" = queue.Queue()

    def _worker():
        async def _pump():
            async for event in stream_agent_events(user, message, "web", history):
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

    def post(self, request):
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response(
                {"detail": "message is required"}, status=status.HTTP_400_BAD_REQUEST
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
