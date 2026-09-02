"""Push a follow-up message to the channel a decision came from.

Used by the reconciliation task: when a write to Wallbit got no answer the
user was told "verificando", and this is how they learn the outcome. Best
effort — a failed notification is logged, never raised, because the decision
row (and the dashboard) already carry the final state.
"""
from __future__ import annotations

import asyncio
import logging
import re

from django.conf import settings

from .models import AgentDecision

logger = logging.getLogger(__name__)

_WA_ID = re.compile(r"wa_(\d{6,20})")
_TG_ID = re.compile(r"(?:^|\|)tg_(\d{3,20})")


def whatsapp_phone_for(external_id: str) -> str | None:
    """``external_id`` holds ``wa_<phone>`` (possibly alongside a Telegram id)."""
    match = _WA_ID.search(external_id or "")
    return match.group(1) if match else None


def telegram_chat_id_for(external_id: str) -> int | None:
    """Telegram users carry either a bare numeric id or a ``tg_<id>`` token."""
    value = external_id or ""
    if value.isdigit():
        return int(value)
    match = _TG_ID.search(value)
    return int(match.group(1)) if match else None


def notify_decision_user(decision: AgentDecision, text: str) -> bool:
    """Send ``text`` through the decision's channel. Returns whether it was sent.

    Web decisions have no push channel: the dashboard reads ``status`` from
    the decision itself.
    """
    external_id = getattr(decision.user, "external_id", "") or ""
    try:
        if decision.channel == AgentDecision.WHATSAPP:
            phone = whatsapp_phone_for(external_id)
            if not phone:
                logger.warning("notify: no WhatsApp id for user %s", decision.user_id)
                return False
            from whatsappbot.views import send_meta_whatsapp_message

            return bool(send_meta_whatsapp_message(phone, text))

        if decision.channel == AgentDecision.TELEGRAM:
            chat_id = telegram_chat_id_for(external_id)
            token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
            if chat_id is None or not token:
                logger.warning("notify: no Telegram chat for user %s", decision.user_id)
                return False
            from telegram import Bot

            async def _send() -> None:
                async with Bot(token) as bot:
                    await bot.send_message(chat_id=chat_id, text=text)

            asyncio.run(_send())
            return True
    except Exception:  # noqa: BLE001 — never let a notification break reconciliation
        logger.exception(
            "notify: failed to reach user for decision %s via %s",
            decision.id,
            decision.channel,
        )
        return False
    return False


__all__ = ["notify_decision_user", "telegram_chat_id_for", "whatsapp_phone_for"]
