"""WhatsApp glue for the Wallbit confirmation flow.

Responsibilities:

- `extract_pending_confirmation(messages)`: scan the LangChain ToolMessages
  produced by the agent for the most recent `requires_confirmation: True`
  result. Returns the preview payload or None.
- `send_confirmation_buttons(phone, decision_id, preview, two_step)`:
  build a Meta WhatsApp interactive `button` message and POST it. Two
  buttons: Confirmar / Cancelar (or "Doble confirmación" when two_step).
- `handle_button_press(button_id, user)`: dispatch a `wallbit_confirm_*`
  or `wallbit_cancel_*` button tap to the executor and return a
  user-facing reply.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from users.models import User
from wallbit.agent_safety import (
    AccountNotConnected,
    get_account_or_raise,
    get_pending_decision,
    mark_failed,
)
from wallbit.executors import UnknownTool, execute_decision
from wallbit.models import AgentDecision

logger = logging.getLogger(__name__)

CONFIRM_PREFIX = "wallbit_confirm_"
CANCEL_PREFIX = "wallbit_cancel_"


def extract_pending_confirmation(messages: list[Any]) -> dict[str, Any] | None:
    """Return the latest tool result that requires confirmation, if any."""
    for msg in reversed(messages):
        if msg.__class__.__name__ != "ToolMessage":
            continue
        raw = getattr(msg, "content", None)
        if not raw:
            continue
        payload = _coerce_payload(raw)
        if isinstance(payload, dict) and payload.get("requires_confirmation"):
            return payload
    return None


def _coerce_payload(content: Any) -> Any:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    return None


def _summary_text(preview: dict[str, Any], two_step: bool) -> str:
    summary = preview.get("summary") or "Operación pendiente"
    header = "⚠️ Doble confirmación requerida" if two_step else "🔒 Confirmación requerida"

    risk_warning = (preview.get("risk_warning") or "").strip()
    risk_block = f"\n\n🛡️ Riesgo: {risk_warning}" if risk_warning else ""

    return f"{header}\n\n{summary}{risk_block}\n\n¿Confirmas?"


def send_confirmation_buttons(
    phone: str, decision_id: int, preview: dict[str, Any], two_step: bool = False
) -> bool:
    phone_number_id = getattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", "")
    access_token = getattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "")
    if not phone_number_id or not access_token:
        logger.error("Meta credentials missing; cannot send confirmation buttons")
        return False

    body_text = _summary_text(preview, two_step)
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


def handle_button_press(button_id: str, user: User) -> str:
    if button_id.startswith(CONFIRM_PREFIX):
        try:
            decision_id = int(button_id[len(CONFIRM_PREFIX):])
        except ValueError:
            return "No reconocí esa acción."
        return _execute(user, decision_id)

    if button_id.startswith(CANCEL_PREFIX):
        try:
            decision_id = int(button_id[len(CANCEL_PREFIX):])
        except ValueError:
            return "No reconocí esa acción."
        return _cancel(user, decision_id)

    return ""


def _execute(user: User, decision_id: int) -> str:
    try:
        decision = get_pending_decision(user, decision_id)
    except AgentDecision.DoesNotExist:
        return "Esa operación ya fue resuelta o no existe."

    try:
        account = get_account_or_raise(user)
    except AccountNotConnected as exc:
        mark_failed(decision, error=str(exc))
        return f"❌ {exc}"

    if account.kill_switch_until and account.kill_switch_until > timezone.now():
        mark_failed(decision, error="kill_switch_active")
        return "🛑 El kill switch de Wallbit está activo. La operación fue cancelada."

    try:
        result = execute_decision(decision, account)
    except UnknownTool as exc:
        mark_failed(decision, error=str(exc))
        return f"❌ Operación desconocida: {exc}"

    if result.get("ok"):
        tx_uuid = result.get("wallbit_tx_uuid")
        suffix = f"\n\n🧾 Tx: `{tx_uuid}`" if tx_uuid else ""
        return f"✅ Operación ejecutada en Wallbit.{suffix}"

    err = result.get("error") or "error desconocido"
    return f"❌ Wallbit rechazó la operación: {err}"


def _cancel(user: User, decision_id: int) -> str:
    try:
        decision = get_pending_decision(user, decision_id)
    except AgentDecision.DoesNotExist:
        return "Esa operación ya fue resuelta o no existe."
    mark_failed(decision, error="cancelled_by_user")
    return "🚫 Operación cancelada."
