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
    action = args["action"].upper()
    symbol = args["symbol"].upper()
    amount_usd = Decimal(str(args["amount_usd"]))

    # TODO: confirm exact Wallbit endpoint shape for trade orders.
    body = {"action": action, "amount_usd": str(amount_usd)}
    with _client(account) as client:
        response = client.post(f"/assets/{symbol}/order", json=body)

    tx_uuid = _extract_tx_uuid(response.data)
    mark_executed(decision, wallbit_tx_uuid=tx_uuid)
    _record_investment(
        user_id=decision.user_id,
        kind=Investment.STOCK,
        action=Investment.BUY if action == "BUY" else Investment.SELL,
        symbol=symbol,
        amount_usd=amount_usd,
        wallbit_tx_uuid=tx_uuid,
    )
    return {"ok": True, "wallbit_tx_uuid": tx_uuid, "data": response.data}


def execute_move_funds(
    decision: AgentDecision, account: WallbitAccount, args: dict[str, Any]
) -> dict[str, Any]:
    source = args["source_currency"].upper()
    dest = args["dest_currency"].upper()
    amount = Decimal(str(args["amount"]))

    # TODO: confirm internal-transfer endpoint shape.
    body = {
        "type": "INTERNAL",
        "source_currency": source,
        "dest_currency": dest,
        "source_amount": str(amount),
    }
    with _client(account) as client:
        response = client.post("/transactions", json=body)

    tx_uuid = _extract_tx_uuid(response.data)
    mark_executed(decision, wallbit_tx_uuid=tx_uuid)
    return {"ok": True, "wallbit_tx_uuid": tx_uuid, "data": response.data}


def execute_deposit_chest(
    decision: AgentDecision, account: WallbitAccount, args: dict[str, Any]
) -> dict[str, Any]:
    chest_id = int(args["chest_id"])
    amount_usd = Decimal(str(args["amount_usd"]))

    # TODO: confirm chest deposit endpoint shape.
    with _client(account) as client:
        response = client.post(
            f"/chests/{chest_id}/deposit", json={"amount_usd": str(amount_usd)}
        )

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
    chest_id = int(args["chest_id"])
    amount_usd = Decimal(str(args["amount_usd"]))

    with _client(account) as client:
        response = client.post(
            f"/chests/{chest_id}/withdraw", json={"amount_usd": str(amount_usd)}
        )

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
    card_id = int(args["card_id"])
    new_status = args["new_status"].upper()  # FROZEN | ACTIVE

    # TODO: confirm card status endpoint shape.
    with _client(account) as client:
        response = client.patch(
            f"/cards/{card_id}/status", json={"status": new_status}
        )

    mark_executed(decision)
    return {"ok": True, "card_id": card_id, "new_status": new_status, "data": response.data}


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
