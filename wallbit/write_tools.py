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


_ACCOUNT_BUCKETS = {"DEFAULT", "INVESTMENT"}


def _preview_move_funds(
    *, user: User, channel: str, user_message: str,
    currency: str, from_account: str, to_account: str, amount: float,
) -> dict[str, Any]:
    currency_norm = (currency or "").upper().strip()
    src = (from_account or "").upper().strip()
    dst = (to_account or "").upper().strip()
    if not currency_norm:
        return {"ok": False, "error": "currency_required",
                "message": "currency es obligatorio."}
    if src not in _ACCOUNT_BUCKETS or dst not in _ACCOUNT_BUCKETS:
        return {"ok": False, "error": "invalid_account",
                "message": "from_account y to_account deben ser DEFAULT o INVESTMENT."}
    if src == dst:
        return {"ok": False, "error": "same_account",
                "message": "Las cuentas de origen y destino son iguales."}

    amount_dec = Decimal(str(amount))
    if amount_dec <= 0:
        return {"ok": False, "error": "amount_invalid",
                "message": "amount debe ser mayor a 0."}

    try:
        account = get_account_or_raise(user)
        check_kill_switch(account)
    except (AccountNotConnected, KillSwitchActive) as exc:
        return _safety_error_payload(exc)

    check = evaluate_move_limits(user, amount_dec)
    if not check.ok:
        return _limits_error_payload(check)

    preview = {
        "currency": currency_norm,
        "from_account": src,
        "to_account": dst,
        "amount": str(amount_dec),
        "summary": f"Mover {amount_dec} {currency_norm} de {src} → {dst}",
    }
    decision = create_pending_decision(
        user=user,
        channel=channel,
        user_message=user_message,
        tool_name="wallbit_move_funds",
        tool_args={
            "currency": currency_norm,
            "from_account": src,
            "to_account": dst,
            "amount": str(amount_dec),
        },
        preview=preview,
    )
    return _pending_payload(decision.id, preview, check.two_step_required)


def _preview_chest(
    *, user: User, channel: str, user_message: str,
    action: str, robo_advisor_id: int, amount_usd: float,
    counter_account: str = "DEFAULT",
    chest_category: str = "",
) -> dict[str, Any]:
    action_norm = action.upper()  # DEPOSIT | WITHDRAW
    amount = Decimal(str(amount_usd))
    if amount <= 0:
        return {"ok": False, "error": "amount_invalid",
                "message": "amount_usd debe ser mayor a 0."}
    if action_norm == "DEPOSIT" and amount < 10:
        return {"ok": False, "error": "amount_too_low",
                "message": "El depósito mínimo en el Robo Advisor es USD 10."}
    counter = (counter_account or "DEFAULT").upper()
    if counter not in _ACCOUNT_BUCKETS:
        return {"ok": False, "error": "invalid_account",
                "message": "La cuenta contraparte debe ser DEFAULT o INVESTMENT."}

    try:
        account = get_account_or_raise(user)
        check_kill_switch(account)
    except (AccountNotConnected, KillSwitchActive) as exc:
        return _safety_error_payload(exc)

    check = evaluate_move_limits(user, amount)
    if not check.ok:
        return _limits_error_payload(check)

    tool_name = "wallbit_deposit_chest" if action_norm == "DEPOSIT" else "wallbit_withdraw_chest"
    tool_args: dict[str, Any] = {
        "robo_advisor_id": int(robo_advisor_id),
        "amount_usd": str(amount),
        "chest_category": chest_category,
    }
    if action_norm == "DEPOSIT":
        tool_args["from_account"] = counter
    else:
        tool_args["to_account"] = counter

    preview = {
        "action": action_norm,
        "robo_advisor_id": int(robo_advisor_id),
        "chest_category": chest_category,
        "amount_usd": str(amount),
        "counter_account": counter,
        "summary": (
            f"{action_norm} USD {amount} "
            f"{'a' if action_norm == 'DEPOSIT' else 'de'} robo #{robo_advisor_id} "
            f"({'from ' if action_norm == 'DEPOSIT' else 'to '}{counter})"
        ),
    }
    decision = create_pending_decision(
        user=user,
        channel=channel,
        user_message=user_message,
        tool_name=tool_name,
        tool_args=tool_args,
        preview=preview,
    )
    return _pending_payload(decision.id, preview, check.two_step_required)


def _preview_set_card_status(
    *, user: User, channel: str, user_message: str,
    card_uuid: str, new_status: str,
) -> dict[str, Any]:
    card_uuid_norm = (card_uuid or "").strip()
    if not card_uuid_norm:
        return {"ok": False, "error": "card_uuid_required",
                "message": "Falta el card_uuid."}
    status_norm = (new_status or "").upper()
    if status_norm not in {"ACTIVE", "SUSPENDED"}:
        return {"ok": False, "error": "invalid_status",
                "message": "new_status debe ser ACTIVE o SUSPENDED."}

    try:
        account = get_account_or_raise(user)
        check_kill_switch(account)
    except (AccountNotConnected, KillSwitchActive) as exc:
        return _safety_error_payload(exc)

    preview = {
        "card_uuid": card_uuid_norm,
        "new_status": status_norm,
        "summary": f"Tarjeta {card_uuid_norm} → {status_norm}",
    }
    decision = create_pending_decision(
        user=user,
        channel=channel,
        user_message=user_message,
        tool_name="wallbit_set_card_status",
        tool_args={"card_uuid": card_uuid_norm, "new_status": status_norm},
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
        currency: str, from_account: str, to_account: str, amount: float
    ) -> dict[str, Any]:
        """Propone mover fondos entre las cuentas internas DEFAULT e INVESTMENT del user en Wallbit (NO ejecuta).

        Wallbit no convierte monedas en este endpoint: solo mueve saldo de una
        misma moneda entre las dos cuentas internas (DEFAULT vs INVESTMENT).

        Args:
            currency: Moneda a mover, ej "USD".
            from_account: DEFAULT o INVESTMENT.
            to_account: DEFAULT o INVESTMENT (distinto al de origen).
            amount: Monto a mover.
        """
        return _preview_move_funds(
            user=user, channel=channel, user_message=user_message,
            currency=currency, from_account=from_account,
            to_account=to_account, amount=amount,
        )

    @tool
    def wallbit_deposit_chest(
        robo_advisor_id: int, amount_usd: float,
        from_account: str = "DEFAULT", chest_category: str = "",
    ) -> dict[str, Any]:
        """Propone depositar USD en un Robo Advisor del user (NO ejecuta).

        Args:
            robo_advisor_id: ID del robo advisor en Wallbit.
            amount_usd: Monto en USD a depositar (mínimo 10).
            from_account: Cuenta origen — DEFAULT o INVESTMENT (default DEFAULT).
            chest_category: Etiqueta opcional para tu historial (EMERGENCIES, VACATIONS...).
        """
        return _preview_chest(
            user=user, channel=channel, user_message=user_message,
            action="DEPOSIT", robo_advisor_id=robo_advisor_id,
            amount_usd=amount_usd, counter_account=from_account,
            chest_category=chest_category,
        )

    @tool
    def wallbit_withdraw_chest(
        robo_advisor_id: int, amount_usd: float,
        to_account: str = "DEFAULT", chest_category: str = "",
    ) -> dict[str, Any]:
        """Propone retirar USD de un Robo Advisor del user (NO ejecuta).

        Args:
            robo_advisor_id: ID del robo advisor en Wallbit.
            amount_usd: Monto en USD a retirar.
            to_account: Cuenta destino — DEFAULT o INVESTMENT (default DEFAULT).
            chest_category: Etiqueta opcional para tu historial.
        """
        return _preview_chest(
            user=user, channel=channel, user_message=user_message,
            action="WITHDRAW", robo_advisor_id=robo_advisor_id,
            amount_usd=amount_usd, counter_account=to_account,
            chest_category=chest_category,
        )

    @tool
    def wallbit_set_card_status(card_uuid: str, new_status: str) -> dict[str, Any]:
        """Propone activar (ACTIVE) o suspender (SUSPENDED) una tarjeta Wallbit (NO ejecuta).

        Args:
            card_uuid: UUID de la tarjeta (string).
            new_status: ACTIVE o SUSPENDED.
        """
        return _preview_set_card_status(
            user=user, channel=channel, user_message=user_message,
            card_uuid=card_uuid, new_status=new_status,
        )

    return [
        wallbit_place_trade,
        wallbit_move_funds,
        wallbit_deposit_chest,
        wallbit_withdraw_chest,
        wallbit_set_card_status,
    ]
