"""Preview-mode tools for Wallbit write operations.

The LLM calls these to *propose* an action. They never hit Wallbit
themselves — they:

1. Resolve the user's connected Wallbit account.
2. Run pre-flight checks (kill switch + AgentLimits).
3. Persist an AgentDecision row.
4. Return a structured preview the bot can render with inline buttons.

The actual Wallbit request happens later from POST /agent/confirm/{id}/,
which calls `executors.execute_decision()`.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from langchain_core.tools import tool

from users.models import User

from .agent_safety import (
    AccountNotConnected,
    KillSwitchActive,
    check_kill_switch,
    create_pending_decision,
    evaluate_move_limits,
    evaluate_trade_limits,
    get_account_or_raise,
)

logger = logging.getLogger(__name__)


def _safety_error_payload(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", "safety_error")
    return {"ok": False, "requires_confirmation": False, "error": code, "message": str(exc)}


def _limits_error_payload(check) -> dict[str, Any]:
    return {
        "ok": False,
        "requires_confirmation": False,
        "error": check.code,
        "message": check.reason,
    }


def _pending_payload(
    decision_id: int, preview: dict[str, Any], two_step_required: bool
) -> dict[str, Any]:
    return {
        "ok": True,
        "requires_confirmation": True,
        "confirmation_id": decision_id,
        "two_step_required": two_step_required,
        "preview": preview,
    }


def _preview_place_trade(
    *, user: User, channel: str, user_message: str,
    action: str, symbol: str, amount_usd: float,
) -> dict[str, Any]:
    action_norm = (action or "").upper()
    if action_norm not in {"BUY", "SELL"}:
        return {"ok": False, "error": "invalid_action",
                "message": "action debe ser BUY o SELL."}
    symbol_norm = (symbol or "").upper().strip()
    if not symbol_norm:
        return {"ok": False, "error": "symbol_required",
                "message": "Falta el símbolo."}

    amount = Decimal(str(amount_usd))
    if amount <= 0:
        return {"ok": False, "error": "amount_invalid",
                "message": "amount_usd debe ser mayor a 0."}

    try:
        account = get_account_or_raise(user)
        check_kill_switch(account)
    except (AccountNotConnected, KillSwitchActive) as exc:
        return _safety_error_payload(exc)

    check = evaluate_trade_limits(user, symbol_norm, amount)
    if not check.ok:
        return _limits_error_payload(check)

    preview = {
        "action": action_norm,
        "symbol": symbol_norm,
        "amount_usd": str(amount),
        "summary": f"{action_norm} {symbol_norm} por USD {amount}",
    }
    decision = create_pending_decision(
        user=user,
        channel=channel,
        user_message=user_message,
        tool_name="wallbit_place_trade",
        tool_args={"action": action_norm, "symbol": symbol_norm,
                   "amount_usd": str(amount)},
        preview=preview,
    )
    return _pending_payload(decision.id, preview, check.two_step_required)


def _preview_move_funds(
    *, user: User, channel: str, user_message: str,
    source_currency: str, dest_currency: str, amount: float,
) -> dict[str, Any]:
    source = (source_currency or "").upper().strip()
    dest = (dest_currency or "").upper().strip()
    if not source or not dest:
        return {"ok": False, "error": "currency_required",
                "message": "source_currency y dest_currency son obligatorios."}
    if source == dest:
        return {"ok": False, "error": "same_currency",
                "message": "Las monedas de origen y destino son iguales."}

    amount_dec = Decimal(str(amount))
    if amount_dec <= 0:
        return {"ok": False, "error": "amount_invalid",
                "message": "amount debe ser mayor a 0."}

    try:
        account = get_account_or_raise(user)
        check_kill_switch(account)
    except (AccountNotConnected, KillSwitchActive) as exc:
        return _safety_error_payload(exc)

    # Approximate USD value for limit eval — assume 1:1 if non-USD source.
    # TODO: convert source -> USD using a Wallbit rate endpoint.
    check = evaluate_move_limits(user, amount_dec)
    if not check.ok:
        return _limits_error_payload(check)

    preview = {
        "source_currency": source,
        "dest_currency": dest,
        "amount": str(amount_dec),
        "summary": f"Mover {amount_dec} {source} → {dest}",
    }
    decision = create_pending_decision(
        user=user,
        channel=channel,
        user_message=user_message,
        tool_name="wallbit_move_funds",
        tool_args={"source_currency": source, "dest_currency": dest,
                   "amount": str(amount_dec)},
        preview=preview,
    )
    return _pending_payload(decision.id, preview, check.two_step_required)


def _preview_chest(
    *, user: User, channel: str, user_message: str,
    action: str, chest_id: int, amount_usd: float,
    chest_category: str = "",
) -> dict[str, Any]:
    action_norm = action.upper()  # DEPOSIT | WITHDRAW
    amount = Decimal(str(amount_usd))
    if amount <= 0:
        return {"ok": False, "error": "amount_invalid",
                "message": "amount_usd debe ser mayor a 0."}

    try:
        account = get_account_or_raise(user)
        check_kill_switch(account)
    except (AccountNotConnected, KillSwitchActive) as exc:
        return _safety_error_payload(exc)

    check = evaluate_move_limits(user, amount)
    if not check.ok:
        return _limits_error_payload(check)

    tool_name = "wallbit_deposit_chest" if action_norm == "DEPOSIT" else "wallbit_withdraw_chest"
    preview = {
        "action": action_norm,
        "chest_id": chest_id,
        "chest_category": chest_category,
        "amount_usd": str(amount),
        "summary": f"{action_norm} USD {amount} {'a' if action_norm == 'DEPOSIT' else 'de'} cofre #{chest_id}",
    }
    decision = create_pending_decision(
        user=user,
        channel=channel,
        user_message=user_message,
        tool_name=tool_name,
        tool_args={"chest_id": chest_id, "amount_usd": str(amount),
                   "chest_category": chest_category},
        preview=preview,
    )
    return _pending_payload(decision.id, preview, check.two_step_required)


def _preview_set_card_status(
    *, user: User, channel: str, user_message: str,
    card_id: int, new_status: str,
) -> dict[str, Any]:
    status_norm = (new_status or "").upper()
    if status_norm not in {"ACTIVE", "FROZEN"}:
        return {"ok": False, "error": "invalid_status",
                "message": "new_status debe ser ACTIVE o FROZEN."}

    try:
        account = get_account_or_raise(user)
        check_kill_switch(account)
    except (AccountNotConnected, KillSwitchActive) as exc:
        return _safety_error_payload(exc)

    preview = {
        "card_id": card_id,
        "new_status": status_norm,
        "summary": f"Tarjeta #{card_id} → {status_norm}",
    }
    decision = create_pending_decision(
        user=user,
        channel=channel,
        user_message=user_message,
        tool_name="wallbit_set_card_status",
        tool_args={"card_id": card_id, "new_status": status_norm},
        preview=preview,
    )
    # Card freeze is high-leverage but doesn't move money; treat as single step.
    return _pending_payload(decision.id, preview, two_step_required=False)


def make_wallbit_write_tools(
    user: User, channel: str, user_message: str
) -> list:
    """Bind user/channel/message so the LLM only sees the trade-shaped args."""

    @tool
    def wallbit_place_trade(action: str, symbol: str, amount_usd: float) -> dict[str, Any]:
        """Propone una orden de compra/venta de un activo en Wallbit (NO ejecuta).

        Devuelve un preview + confirmation_id. El usuario debe confirmar
        con un botón en el chat antes de que se envíe a Wallbit.

        Args:
            action: BUY o SELL.
            symbol: Ticker del activo, ej "AAPL".
            amount_usd: Monto en USD a operar.
        """
        return _preview_place_trade(
            user=user, channel=channel, user_message=user_message,
            action=action, symbol=symbol, amount_usd=amount_usd,
        )

    @tool
    def wallbit_move_funds(
        source_currency: str, dest_currency: str, amount: float
    ) -> dict[str, Any]:
        """Propone mover fondos entre dos cuentas/monedas internas del user en Wallbit (NO ejecuta).

        Args:
            source_currency: Moneda de origen, ej "USD".
            dest_currency: Moneda de destino, ej "ARS".
            amount: Monto en la moneda de origen.
        """
        return _preview_move_funds(
            user=user, channel=channel, user_message=user_message,
            source_currency=source_currency, dest_currency=dest_currency,
            amount=amount,
        )

    @tool
    def wallbit_deposit_chest(
        chest_id: int, amount_usd: float, chest_category: str = ""
    ) -> dict[str, Any]:
        """Propone depositar USD en un Chest (cofre de roboadvisor) del user (NO ejecuta).

        Args:
            chest_id: ID del chest en Wallbit.
            amount_usd: Monto en USD a depositar.
            chest_category: Etiqueta opcional (EMERGENCIES, VACATIONS...).
        """
        return _preview_chest(
            user=user, channel=channel, user_message=user_message,
            action="DEPOSIT", chest_id=chest_id, amount_usd=amount_usd,
            chest_category=chest_category,
        )

    @tool
    def wallbit_withdraw_chest(
        chest_id: int, amount_usd: float, chest_category: str = ""
    ) -> dict[str, Any]:
        """Propone retirar USD de un Chest (cofre) del user (NO ejecuta)."""
        return _preview_chest(
            user=user, channel=channel, user_message=user_message,
            action="WITHDRAW", chest_id=chest_id, amount_usd=amount_usd,
            chest_category=chest_category,
        )

    @tool
    def wallbit_set_card_status(card_id: int, new_status: str) -> dict[str, Any]:
        """Propone congelar (FROZEN) o reactivar (ACTIVE) una tarjeta Wallbit (NO ejecuta).

        Args:
            card_id: ID de la tarjeta.
            new_status: ACTIVE o FROZEN.
        """
        return _preview_set_card_status(
            user=user, channel=channel, user_message=user_message,
            card_id=card_id, new_status=new_status,
        )

    return [
        wallbit_place_trade,
        wallbit_move_funds,
        wallbit_deposit_chest,
        wallbit_withdraw_chest,
        wallbit_set_card_status,
    ]
