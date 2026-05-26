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
    get_user_categories_with_details,
    get_user_expense_categories,
    get_user_income_categories,
)
from telegrambot.config import AGENT_MAX_ITERATIONS, AGENT_EXECUTION_TIMEOUT, ERROR_MESSAGES
from users.models import User
from whatsappbot.wallbit_handlers import extract_pending_confirmation

from .retry import retry_with_backoff
from .supervisor import build_supervisor  # returns (agent, pending_container)

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
    """Run the Tresqu supervisor on the given message.

    Args:
        user: authenticated Tresqu user (already resolved by the bot).
        raw_text: the message text to process (audio already transcribed).
        channel: ``"telegram"`` or ``"whatsapp"`` — forwarded to the Wallbit
            subagent so write-tool previews are tagged with the right origin.
        history: list of LangChain messages with prior turns.

    Returns:
        ``AgentResponse`` with the assistant text and an optional pending
        Wallbit confirmation that the bot should surface as a UI button.
    """

    try:
        expense_categories_str, income_categories_str = await _load_categories(user)
        current_date = _user_today(user)

        supervisor, pending_container = build_supervisor(
            user=user,
            channel=channel,
            user_message=raw_text,
            expense_categories_str=expense_categories_str,
            income_categories_str=income_categories_str,
            current_date=current_date,
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
        # Subagent-emitted ToolMessages do not bubble up to the supervisor's
        # message list, so prefer the container captured inside the Wallbit
        # subagent wrapper. Fall back to scanning top-level messages in case
        # a future direct tool wires the preview at this level.
        pending = pending_container.get("confirmation") or extract_pending_confirmation(
            result["messages"]
        )
        return AgentResponse(text=text, pending_confirmation=pending)

    except Exception as exc:
        logger.exception(f"process_message unexpected error: {exc}")
        return AgentResponse(
            text="Lo siento, hubo un error inesperado. Por favor, intenta de nuevo."
        )
