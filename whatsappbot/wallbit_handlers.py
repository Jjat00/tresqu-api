"""WhatsApp glue for the Wallbit confirmation flow.

The channel-agnostic lifecycle (preview text, execute, cancel,
button-id parsing) lives in ``wallbit.confirmation_actions`` and is
shared with Telegram. This module only handles the WhatsApp-specific
HTTP call against Meta's Cloud API and re-exports the helpers that
``agents/services.py`` already imports from here.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from users.models import User
from wallbit.confirmation_actions import (
    CANCEL_PREFIX,
    CONFIRM_PREFIX,
    extract_pending_confirmation,
    extract_pending_confirmations,
    handle_button_press,
    summary_text,
)

logger = logging.getLogger(__name__)


def send_confirmation_buttons(
    phone: str, decision_id: int, preview: dict[str, Any], two_step: bool = False
) -> bool:
    phone_number_id = getattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", "")
    access_token = getattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "")
    if not phone_number_id or not access_token:
        logger.error("Meta credentials missing; cannot send confirmation buttons")
        return False

    body_text = summary_text(preview, two_step)
    confirm_label = "Confirmar 2x" if two_step else "Confirmar"

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text[:1024]},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"{CONFIRM_PREFIX}{decision_id}",
                            "title": confirm_label[:20],
                        },
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"{CANCEL_PREFIX}{decision_id}",
                            "title": "Cancelar",
                        },
                    },
                ]
            },
        },
    }

    url = f"https://graph.facebook.com/v23.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as exc:
        logger.warning("send_confirmation_buttons HTTP failed: %s", exc)
        return False

    if resp.status_code != 200:
        logger.error(
            "send_confirmation_buttons non-200: %s %s",
            resp.status_code,
            resp.text[:300],
        )
        return False
    logger.info("Sent confirmation buttons for decision %s to %s", decision_id, phone)
    return True


def handle_button_press_for_user(button_id: str, user: User) -> str:
    """Thin alias kept for backward compatibility with existing WhatsApp wiring."""
    return handle_button_press(button_id, user)


__all__ = [
    "CANCEL_PREFIX",
    "CONFIRM_PREFIX",
    "extract_pending_confirmation",
    "extract_pending_confirmations",
    "handle_button_press",
    "handle_button_press_for_user",
    "send_confirmation_buttons",
]
