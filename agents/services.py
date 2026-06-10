"""Unified entry point for the Tresqu multi-agent system.

Both Telegram and WhatsApp services call ``process_message`` after handling
their channel-specific bits (transcription, image extraction, message
fan-out). This module is channel-agnostic: it loads the user context, builds
the supervisor, invokes it with retry/backoff, and returns either plain text
or a pending Wallbit confirmation that the caller can render appropriately.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import pytz
from asgiref.sync import sync_to_async
from django.utils import timezone
from langchain_core.messages import HumanMessage

from categories.utils import (
    get_user_expense_categories,
    get_user_income_categories,
)
from telegrambot.config import AGENT_MAX_ITERATIONS, AGENT_EXECUTION_TIMEOUT, ERROR_MESSAGES
from users.models import User
from whatsappbot.wallbit_handlers import extract_pending_confirmation

from . import risk_profiler_service
from .conversation_memory import get_semantic_context
from .retry import retry_with_backoff
from .supervisor import build_supervisor

RISK_PROFILE_COMMAND = "/perfil"
RESUME_PROFILE_HINT = (
    "\n\n_¿Seguimos con tu perfil de inversión? Responde la pregunta que te hice "
    "o dime *cancelar* si prefieres dejarlo para después._"
)

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    """Result of running the supervisor on a user message."""

    text: str
    pending_confirmation: dict[str, Any] | None = None


@sync_to_async
def _load_categories(user: User) -> tuple[str, str]:
    """Returns ``(expense_categories_str, income_categories_str)`` for the prompt."""

    expenses = get_user_expense_categories(user)
    incomes = get_user_income_categories(user)
    expense_str = "Gastos: " + ", ".join(expenses) if expenses else "Gastos: (sin categorías personales aún)"
    income_str = "Ingresos: " + ", ".join(incomes) if incomes else "Ingresos: (sin categorías personales aún)"
    return expense_str, income_str


def _user_today(user: User) -> str:
    """Today's date in the user's timezone, formatted ``YYYY-MM-DD``.

    The supervisor LLM cannot be trusted to know the real current date, so
    we inject it explicitly into the prompt for every turn.
    """

    try:
        tz = pytz.timezone(getattr(user, "timezone", None) or "UTC")
    except pytz.exceptions.UnknownTimeZoneError:
        tz = pytz.UTC
    return timezone.now().astimezone(tz).strftime("%Y-%m-%d")


async def process_message(
    user: User,
    raw_text: str,
    channel: str,
    history: list,
) -> AgentResponse:
    """Run the Tresqu agent stack on the given message.

    Routing order:
    1. ``/perfil`` always starts a fresh risk-profiler session (overwrites any prior).
    2. If a risk-profiler session is paused, classify the message: it can be
       an answer to the pending question, an unrelated intent (handled by the
       supervisor while keeping the session paused), or an explicit cancel.
    3. Otherwise, hand off to the multi-agent supervisor as usual.
    """

    try:
        normalized = raw_text.strip().lower()
        if normalized == RISK_PROFILE_COMMAND:
            return await _resume_or_start_profiler(
                user, channel, start_fresh=True, user_message=raw_text, history=history
            )
        if await risk_profiler_service.is_session_active(user.id):
            return await _route_with_active_session(user, channel, raw_text, history)

        return await _run_supervisor(user, channel, raw_text, history)

    except Exception as exc:
        logger.exception(f"process_message unexpected error: {exc}")
        return AgentResponse(
            text="Lo siento, hubo un error inesperado. Por favor, intenta de nuevo."
        )


async def _route_with_active_session(
    user: User,
    channel: str,
    raw_text: str,
    history: list,
) -> AgentResponse:
    """Classify a message arriving during an open Q&A and route accordingly."""

    pending_question = await risk_profiler_service.get_pending_question(user.id)
    if not pending_question:
        # Race / stale check — treat as no session.
        return await _run_supervisor(user, channel, raw_text, history)

    classification = await risk_profiler_service.classify_message(pending_question, raw_text)
    kind = classification.get("type", "qa_answer")
    logger.info(
        f"risk_profiler routing for user={user.id}: kind={kind} "
        f"confidence={classification.get('confidence')}"
    )

    if kind == "cancel":
        await risk_profiler_service.abandon_session(user.id)
        return AgentResponse(
            text=(
                "Listo, dejamos tu perfil para después. Cuando quieras retomarlo, "
                "escríbeme /perfil o pídeme que evalúe tu perfil de riesgo."
            )
        )

    if kind == "other_intent":
        # Process via supervisor without advancing the Q&A. Append a hint so
        # the user knows the profiler is still pending.
        supervisor_response = await _run_supervisor(user, channel, raw_text, history)
        return AgentResponse(
            text=(supervisor_response.text or "") + RESUME_PROFILE_HINT,
            pending_confirmation=supervisor_response.pending_confirmation,
        )

    # qa_answer (default)
    return await _resume_or_start_profiler(
        user, channel, start_fresh=False, user_message=raw_text, history=history
    )


async def _resume_or_start_profiler(
    user: User,
    channel: str,
    *,
    start_fresh: bool,
    user_message: str,
    history: list,
) -> AgentResponse:
    """Start or advance the risk-profiler graph and surface the next step."""

    try:
        if start_fresh:
            step = await risk_profiler_service.start_session(user.id, channel)
        else:
            step = await risk_profiler_service.resume_session(user.id, user_message)
    except Exception as exc:
        logger.exception(f"risk profiler failed for user={user.id}: {exc}")
        return AgentResponse(
            text=(
                "Hubo un problema con tu perfil de inversión. Inténtalo de nuevo "
                "más tarde escribiendo /perfil."
            )
        )

    if step.get("done"):
        return AgentResponse(text=step.get("final_text") or "✅ Tu perfil quedó guardado.")

    question = step.get("question") or ""
    return AgentResponse(text=question)


async def _run_supervisor(
    user: User,
    channel: str,
    raw_text: str,
    history: list,
) -> AgentResponse:
    """Invoke the multi-agent supervisor (no risk-profiler interception)."""

    expense_categories_str, income_categories_str = await _load_categories(user)
    current_date = _user_today(user)

    # Memoria semántica: mensajes antiguos (fuera de la ventana del historial)
    # relevantes al mensaje entrante. get_semantic_context nunca lanza.
    semantic_context = await sync_to_async(get_semantic_context)(user, raw_text)

    supervisor, pending_container, risk_profiler_signal = build_supervisor(
        user=user,
        channel=channel,
        user_message=raw_text,
        expense_categories_str=expense_categories_str,
        income_categories_str=income_categories_str,
        current_date=current_date,
        semantic_context=semantic_context,
    )

    messages = list(history) + [HumanMessage(content=raw_text)]

    async def _invoke():
        return await asyncio.wait_for(
            supervisor.ainvoke(
                {"messages": messages},
                config={"recursion_limit": AGENT_MAX_ITERATIONS},
            ),
            timeout=float(AGENT_EXECUTION_TIMEOUT),
        )

    try:
        result = await retry_with_backoff(_invoke)
    except asyncio.TimeoutError:
        logger.error("Supervisor timeout")
        return AgentResponse(text=ERROR_MESSAGES["timeout"])
    except Exception as exc:
        error_str = str(exc).lower()
        if "timeout" in error_str:
            return AgentResponse(text=ERROR_MESSAGES["timeout"])
        if any(p in error_str for p in ("ssl", "eof detected")):
            return AgentResponse(text=ERROR_MESSAGES["ssl"])
        if any(p in error_str for p in ("connection", "network")):
            return AgentResponse(text=ERROR_MESSAGES["connection"])
        logger.exception(f"Supervisor failed: {exc}")
        return AgentResponse(text=ERROR_MESSAGES["default"])

    text = result["messages"][-1].content or ""

    first_step = risk_profiler_signal.get("first_step")
    if first_step and not first_step.get("done"):
        return AgentResponse(text=first_step.get("question") or text)

    pending = pending_container.get("confirmation") or extract_pending_confirmation(
        result["messages"]
    )
    return AgentResponse(text=text, pending_confirmation=pending)
