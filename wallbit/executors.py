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


def _fetch_current_price(client: WallbitClient, symbol: str) -> Decimal | None:
    """Current price for ``symbol`` from Wallbit, or None on any failure."""
    try:
        resp = client.get(f"/assets/{symbol}")
    except WallbitError:
        return None
    data = resp.data.get("data") if isinstance(resp.data, dict) else None
    if not isinstance(data, dict):
        return None
    raw = data.get("price")
    try:
        return Decimal(str(raw)) if raw is not None else None
    except (ArithmeticError, ValueError, TypeError):
        return None


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
    # LIMIT orders carry a price ceiling/floor and a validity window. Some
    # freshly-listed assets (e.g. SPCX) ONLY accept LIMIT — the preview resolves
    # those fields and persists them in tool_args, so we just forward them.
    if order_type == "LIMIT":
        body["limit_price"] = float(Decimal(str(args["limit_price"])))
        body["time_in_force"] = (args.get("time_in_force") or "DAY").upper()
    with _client(account) as client:
        try:
            response = client.post("/trades", json=body)
        except WallbitError as exc:
            # Some freshly-listed assets (e.g. SPCX right after its IPO) reject
            # MARKET orders with an opaque error because they only accept LIMIT,
            # and Wallbit no longer flags that restriction in the asset metadata
            # — so we can't catch it up front. Fall back ONCE to a LIMIT order at
            # (near) the current price. The USD amount is unchanged, so the user's
            # spend and all pre-flight limits still hold; only the fill mechanism
            # changes. A LIMIT order already gets the same retry → no re-fallback.
            price = None if order_type != "MARKET" else _fetch_current_price(client, symbol)
            if price is None or price <= 0:
                raise
            # BUY caps a hair above market, SELL a hair below, so it still fills.
            buffer = Decimal("1.02") if direction == "BUY" else Decimal("0.98")
            limit_price = (price * buffer).quantize(Decimal("0.01"))
            body = {
                **body,
                "order_type": "LIMIT",
                "limit_price": float(limit_price),
                "time_in_force": "DAY",
            }
            logger.info(
                "place_trade MARKET rejected for %s (%s); retrying as LIMIT @ %s",
                symbol, exc, limit_price,
            )
            response = client.post("/trades", json=body)

    tx_uuid = _extract_tx_uuid(response.data)
    mark_executed(decision, wallbit_tx_uuid=tx_uuid)
    # Don't create an optimistic Investment here. A freshly placed trade is
    # often still PENDING upstream (and Wallbit may not return its uuid on the
    # POST, so we couldn't link it). An unlinked, pre-settlement row both
    # duplicates the row the sync later creates AND inflates "capital invertido"
    # against a live value that excludes pending trades → false loss. Instead,
    # let the sync be the single source of truth and trigger it immediately so
    # the trade shows up (with real shares + status) within seconds.
    try:
        from .tasks import sync_wallbit_transactions

        sync_wallbit_transactions.delay(account.id)
    except Exception as exc:  # noqa: BLE001 — never fail the trade on a dispatch hiccup
        logger.warning("post-trade wallbit sync dispatch failed: %s", exc)
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


def execute_resume_bot(
    decision: AgentDecision, account: WallbitAccount, args: dict[str, Any]  # noqa: ARG001
) -> dict[str, Any]:
    """Clear the kill switch — purely local, no Wallbit API call.

    This executor must run even when ``account.kill_switch_until`` is
    active, because that's the whole point. The confirm view treats it
    as an exception (see ``LOCAL_ONLY_TOOLS``).
    """
    account.kill_switch_until = None
    account.save(update_fields=["kill_switch_until"])
    mark_executed(decision)
    return {"ok": True, "resumed": True}


# Tools that only touch our DB (not Wallbit's API). They bypass the
# kill-switch refusal in AgentConfirmView so the user can lift their own
# pause via chat.
LOCAL_ONLY_TOOLS = frozenset({"wallbit_resume"})


EXECUTORS: dict[str, Callable[..., dict[str, Any]]] = {
    "wallbit_place_trade": execute_place_trade,
    "wallbit_move_funds": execute_move_funds,
    "wallbit_deposit_chest": execute_deposit_chest,
    "wallbit_withdraw_chest": execute_withdraw_chest,
    "wallbit_set_card_status": execute_set_card_status,
    "wallbit_resume": execute_resume_bot,
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
