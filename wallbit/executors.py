"""Per-tool executors invoked from POST /api/wallbit/agent/confirm/{id}/.

Each executor is a pure function: (decision, account, args) -> result dict.
The dispatcher reads `decision.tools_called[0]` to know which tool ran.

If you add a new write tool, register it in EXECUTORS below.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Callable

from .agent_safety import mark_executed, mark_failed
from .client import WallbitClient, WallbitError
from .crypto import decrypt_api_key
from .models import AgentDecision, Investment, WallbitAccount, WallbitTxMirror

logger = logging.getLogger(__name__)


class UnknownTool(Exception):
    pass


def _client(account: WallbitAccount) -> WallbitClient:
    return WallbitClient(decrypt_api_key(account.encrypted_api_key))


def _extract_tx_uuid(payload: Any) -> str:
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        if isinstance(data, dict):
            for key in ("uuid", "id", "tx_uuid", "transaction_id"):
                if data.get(key):
                    return str(data[key])
    return ""


def _record_investment(
    *,
    user_id: int,
    kind: str,
    action: str,
    symbol: str = "",
    chest_category: str = "",
    amount_usd: Decimal,
    shares: Decimal | None = None,
    wallbit_tx_uuid: str = "",
) -> Investment:
    tx_mirror = None
    if wallbit_tx_uuid:
        tx_mirror = WallbitTxMirror.objects.filter(wallbit_uuid=wallbit_tx_uuid).first()
    return Investment.objects.create(
        user_id=user_id,
        kind=kind,
        action=action,
        symbol=symbol,
        chest_category=chest_category,
        amount_usd=amount_usd,
        shares=shares,
        wallbit_tx=tx_mirror,
    )


def execute_place_trade(
    decision: AgentDecision, account: WallbitAccount, args: dict[str, Any]
) -> dict[str, Any]:
    direction = args["action"].upper()  # BUY | SELL
    symbol = args["symbol"].upper()
    amount_usd = Decimal(str(args["amount_usd"]))
    currency = (args.get("currency") or "USD").upper()
    order_type = (args.get("order_type") or "MARKET").upper()

    body: dict[str, Any] = {
        "symbol": symbol,
        "direction": direction,
        "currency": currency,
        "order_type": order_type,
        "amount": float(amount_usd),
    }
    with _client(account) as client:
        response = client.post("/trades", json=body)

    tx_uuid = _extract_tx_uuid(response.data)
    mark_executed(decision, wallbit_tx_uuid=tx_uuid)
    _record_investment(
        user_id=decision.user_id,
        kind=Investment.STOCK,
        action=Investment.BUY if direction == "BUY" else Investment.SELL,
        symbol=symbol,
        amount_usd=amount_usd,
        wallbit_tx_uuid=tx_uuid,
    )
    return {"ok": True, "wallbit_tx_uuid": tx_uuid, "data": response.data}


def execute_move_funds(
    decision: AgentDecision, account: WallbitAccount, args: dict[str, Any]
) -> dict[str, Any]:
    currency = args["currency"].upper()
    from_account = args["from_account"].upper()  # DEFAULT | INVESTMENT
    to_account = args["to_account"].upper()  # DEFAULT | INVESTMENT
    amount = Decimal(str(args["amount"]))

    body = {
        "currency": currency,
        "from": from_account,
        "to": to_account,
        "amount": float(amount),
    }
    with _client(account) as client:
        response = client.post("/operations/internal", json=body)

    tx_uuid = _extract_tx_uuid(response.data)
    mark_executed(decision, wallbit_tx_uuid=tx_uuid)
    return {"ok": True, "wallbit_tx_uuid": tx_uuid, "data": response.data}


def execute_deposit_chest(
    decision: AgentDecision, account: WallbitAccount, args: dict[str, Any]
) -> dict[str, Any]:
    robo_advisor_id = int(args["robo_advisor_id"])
    amount_usd = Decimal(str(args["amount_usd"]))
    from_account = (args.get("from_account") or "DEFAULT").upper()

    body = {
        "robo_advisor_id": robo_advisor_id,
        "amount": float(amount_usd),
        "from": from_account,
    }
    with _client(account) as client:
        response = client.post("/roboadvisor/deposit", json=body)

    tx_uuid = _extract_tx_uuid(response.data)
    mark_executed(decision, wallbit_tx_uuid=tx_uuid)
    _record_investment(
        user_id=decision.user_id,
        kind=Investment.CHEST,
        action=Investment.DEPOSIT,
        chest_category=args.get("chest_category", ""),
        amount_usd=amount_usd,
        wallbit_tx_uuid=tx_uuid,
    )
    return {"ok": True, "wallbit_tx_uuid": tx_uuid, "data": response.data}


def execute_withdraw_chest(
    decision: AgentDecision, account: WallbitAccount, args: dict[str, Any]
) -> dict[str, Any]:
    robo_advisor_id = int(args["robo_advisor_id"])
    amount_usd = Decimal(str(args["amount_usd"]))
    to_account = (args.get("to_account") or "DEFAULT").upper()

    body = {
        "robo_advisor_id": robo_advisor_id,
        "amount": float(amount_usd),
        "to": to_account,
    }
    with _client(account) as client:
        response = client.post("/roboadvisor/withdraw", json=body)

    tx_uuid = _extract_tx_uuid(response.data)
    mark_executed(decision, wallbit_tx_uuid=tx_uuid)
    _record_investment(
        user_id=decision.user_id,
        kind=Investment.CHEST,
        action=Investment.WITHDRAW,
        chest_category=args.get("chest_category", ""),
        amount_usd=amount_usd,
        wallbit_tx_uuid=tx_uuid,
    )
    return {"ok": True, "wallbit_tx_uuid": tx_uuid, "data": response.data}


def execute_set_card_status(
    decision: AgentDecision, account: WallbitAccount, args: dict[str, Any]
) -> dict[str, Any]:
    card_uuid = str(args["card_uuid"])
    new_status = args["new_status"].upper()  # ACTIVE | SUSPENDED

    with _client(account) as client:
        response = client.patch(
            f"/cards/{card_uuid}/status", json={"status": new_status}
        )

    mark_executed(decision)
    return {
        "ok": True,
        "card_uuid": card_uuid,
        "new_status": new_status,
        "data": response.data,
    }


EXECUTORS: dict[str, Callable[..., dict[str, Any]]] = {
    "wallbit_place_trade": execute_place_trade,
    "wallbit_move_funds": execute_move_funds,
    "wallbit_deposit_chest": execute_deposit_chest,
    "wallbit_withdraw_chest": execute_withdraw_chest,
    "wallbit_set_card_status": execute_set_card_status,
}


def execute_decision(
    decision: AgentDecision, account: WallbitAccount
) -> dict[str, Any]:
    if not decision.tools_called:
        raise UnknownTool("decision has no recorded tool call")
    call = decision.tools_called[0]
    tool_name = call.get("tool")
    args = call.get("args", {})
    executor = EXECUTORS.get(tool_name)
    if executor is None:
        raise UnknownTool(f"no executor for {tool_name}")

    try:
        return executor(decision, account, args)
    except WallbitError as exc:
        logger.warning(
            "wallbit execute failed",
            exc_info=exc,
            extra={"decision_id": decision.id, "tool": tool_name},
        )
        mark_failed(decision, error=str(exc))
        return {"ok": False, "error": str(exc), "status": getattr(exc, "status", None)}
    except Exception as exc:  # defensive — log and surface
        logger.exception("wallbit execute crash", extra={"decision_id": decision.id})
        mark_failed(decision, error=str(exc))
        return {"ok": False, "error": str(exc)}
