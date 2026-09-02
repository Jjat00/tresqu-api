"""Channel-agnostic helpers for the Wallbit confirmation lifecycle.

The bot adapters (``whatsappbot/wallbit_handlers``, ``telegrambot/
wallbit_handlers``) own only the platform-specific transport — building
WhatsApp interactive payloads vs. Telegram InlineKeyboardMarkup — and
delegate to this module for everything else:

- Scanning the agent's ToolMessages for a pending confirmation
- Building the user-visible summary text (header + risk warning + body)
- Resolving a button id to (execute | cancel)
- Running ``execute_decision`` with the kill-switch bypass for
  ``LOCAL_ONLY_TOOLS`` (so a paused user can resume from chat)

Keeping this here means both channels stay in sync as the lifecycle
evolves.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from django.utils import timezone

from users.models import User

from .agent_safety import (
    AccountNotConnected,
    claim_pending_decision,
    get_account_or_raise,
    get_pending_decision,
    mark_cancelled,
    mark_failed,
)
from .executors import LOCAL_ONLY_TOOLS, UnknownTool, execute_decision
from .models import AgentDecision

logger = logging.getLogger(__name__)

CONFIRM_PREFIX = "wallbit_confirm_"
CANCEL_PREFIX = "wallbit_cancel_"


def _coerce_payload(content: Any) -> Any:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    return None


def extract_pending_confirmations(messages: list[Any]) -> list[dict[str, Any]]:
    """Every tool result that requires confirmation, oldest first, one per decision.

    A single turn can propose several writes ("compra 20 USD en Google y 20 en
    Meta" → two decisions). Each one needs its own buttons: on 2026-09-02 only
    the last proposal got buttons and the other was silently dropped.
    """
    found: list[dict[str, Any]] = []
    seen: set[Any] = set()
    for msg in messages:
        if msg.__class__.__name__ != "ToolMessage":
            continue
        raw = getattr(msg, "content", None)
        if not raw:
            continue
        payload = _coerce_payload(raw)
        if not (isinstance(payload, dict) and payload.get("requires_confirmation")):
            continue
        key = payload.get("confirmation_id")
        if key is not None and key in seen:
            continue
        seen.add(key)
        found.append(payload)
    return found


def extract_pending_confirmation(messages: list[Any]) -> dict[str, Any] | None:
    """Return the latest tool result that requires confirmation, if any."""
    pending = extract_pending_confirmations(messages)
    return pending[-1] if pending else None


def summary_text(preview: dict[str, Any], two_step: bool) -> str:
    """Build the human-visible body for a confirmation prompt."""
    summary = preview.get("summary") or "Operación pendiente"
    header = "⚠️ Doble confirmación requerida" if two_step else "🔒 Confirmación requerida"

    risk_warning = (preview.get("risk_warning") or "").strip()
    risk_block = f"\n\n🛡️ Riesgo: {risk_warning}" if risk_warning else ""

    return f"{header}\n\n{summary}{risk_block}\n\n¿Confirmas?"


def handle_button_press(button_id: str, user: User) -> str:
    """Dispatch a callback/button id to the matching action."""
    if button_id.startswith(CONFIRM_PREFIX):
        try:
            decision_id = int(button_id[len(CONFIRM_PREFIX):])
        except ValueError:
            return "No reconocí esa acción."
        return execute_confirmed_decision(user, decision_id)

    if button_id.startswith(CANCEL_PREFIX):
        try:
            decision_id = int(button_id[len(CANCEL_PREFIX):])
        except ValueError:
            return "No reconocí esa acción."
        return cancel_pending_decision(user, decision_id)

    return ""


UNCERTAIN_REPLY = (
    "⏳ Wallbit no confirmó la operación a tiempo. NO la reintenté, para no "
    "duplicarla. Estoy verificando en tu historial de Wallbit si se ejecutó y "
    "te aviso en un par de minutos. Mientras tanto no la vuelvas a pedir."
)


def execute_confirmed_decision(user: User, decision_id: int) -> str:
    # Claim under a row lock: a second confirmation of the same decision (double
    # tap, Meta redelivery, Celery redelivery) gets DoesNotExist and never
    # reaches Wallbit.
    try:
        decision = claim_pending_decision(user, decision_id)
    except AgentDecision.DoesNotExist:
        return "Esa operación ya fue resuelta, está en proceso o no existe."

    try:
        account = get_account_or_raise(user)
    except AccountNotConnected as exc:
        mark_failed(decision, error=str(exc))
        return f"❌ {exc}"

    # Tools in LOCAL_ONLY_TOOLS (currently wallbit_resume) must be allowed
    # through even when the kill switch is active — they're how the user
    # lifts the pause from chat. Other writes still get blocked.
    tool_name = (decision.tools_called or [{}])[0].get("tool", "")
    if (
        tool_name not in LOCAL_ONLY_TOOLS
        and account.kill_switch_until
        and account.kill_switch_until > timezone.now()
    ):
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

    if result.get("uncertain"):
        # Never call this "rechazada": on 2026-09-02 four fills were reported
        # as a rejection and the user kept re-ordering.
        return UNCERTAIN_REPLY

    err = result.get("error") or "error desconocido"
    return f"❌ Wallbit rechazó la operación: {err}"


def cancel_pending_decision(user: User, decision_id: int) -> str:
    try:
        decision = get_pending_decision(user, decision_id)
    except AgentDecision.DoesNotExist:
        return "Esa operación ya fue resuelta o no existe."
    mark_cancelled(decision)
    return "🚫 Operación cancelada."


__all__ = [
    "CANCEL_PREFIX",
    "CONFIRM_PREFIX",
    "UNCERTAIN_REPLY",
    "extract_pending_confirmations",
    "cancel_pending_decision",
    "execute_confirmed_decision",
    "extract_pending_confirmation",
    "handle_button_press",
    "summary_text",
]
