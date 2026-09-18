"""Per-tool executors invoked from POST /api/wallbit/agent/confirm/{id}/.

Each executor is a pure function: (decision, account, args) -> result dict.
The dispatcher reads `decision.tools_called[0]` to know which tool ran.

If you add a new write tool, register it in EXECUTORS below.
"""
from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal
from typing import Any, Callable

from .agent_safety import mark_executed, mark_failed, mark_uncertain
from .client import (
    WallbitClient,
    WallbitError,
    WallbitUncertainError,
    WallbitValidationError,
)
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


# Wallbit accepts fractional shares; LIMIT orders are sized in shares, so we
# derive them from the confirmed USD amount at this precision, rounding DOWN.
_LIMIT_SHARES_STEP = Decimal("0.0001")


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
    }
    if order_type == "LIMIT":
        # Wallbit sizes LIMIT orders in shares, not USD ("The shares field is
        # required when order type is LIMIT"). Derive them from the confirmed
        # amount, rounded DOWN so the spend can never exceed what the user saw.
        limit_price = Decimal(str(args["limit_price"]))
        shares = (amount_usd / limit_price).quantize(_LIMIT_SHARES_STEP, rounding=ROUND_DOWN)
        if shares <= 0:
            raise WallbitValidationError(
                f"USD {amount_usd} no alcanza ni una fracción mínima de {symbol} "
                f"a {limit_price} USD."
            )
        body["shares"] = float(shares)
        body["limit_price"] = float(limit_price)
        body["time_in_force"] = (args.get("time_in_force") or "DAY").upper()
    else:
        body["amount"] = float(amount_usd)

    # ONE request, no fallback. This used to retry a rejected MARKET order as a
    # LIMIT order with a second POST — after a *timeout*, i.e. when the first
    # order may already have filled — and the client itself retried the POST up
    # to 4 times. On 2026-09-02 that placed the same 20 USD SPCX order 4 times.
    # A transport error / 5xx now surfaces as WallbitUncertainError and is
    # settled by reconciliation, never by resending.
    with _client(account) as client:
        response = client.post("/trades", json=body)

    tx_uuid = _extract_tx_uuid(response.data)
    mark_executed(decision, wallbit_tx_uuid=tx_uuid)
    # Don't create an optimistic Investment here. A freshly placed trade is
    # often still PENDING upstream (and Wallbit may not return its uuid on the
    # POST, so we couldn't link it). The sync (dispatched by execute_decision
    # for every attempt) is the single source of truth for shares + status.
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


def _dispatch_sync(account_id: int) -> None:
    """Refresh the mirror right away so the dashboard reconciles within seconds.

    Runs after EVERY attempt against Wallbit — success, rejection or unknown —
    because a fill can exist upstream even when we think the order failed.
    """
    try:
        from .tasks import sync_wallbit_transactions

        sync_wallbit_transactions.delay(account_id)
    except Exception as exc:  # noqa: BLE001 — never fail the trade on a dispatch hiccup
        logger.warning("post-trade wallbit sync dispatch failed: %s", exc)


# Seconds before the first reconciliation attempt. Wallbit's fill shows up in
# /transactions shortly after the (slow) POST returns upstream.
RECONCILE_FIRST_DELAY_SECONDS = 20


def _schedule_reconciliation(decision: AgentDecision) -> None:
    try:
        from .tasks import reconcile_uncertain_decision

        reconcile_uncertain_decision.apply_async(
            args=(decision.id,), countdown=RECONCILE_FIRST_DELAY_SECONDS
        )
    except Exception:  # noqa: BLE001 — the beat-driven sync still picks up the fill
        logger.exception(
            "could not schedule reconciliation", extra={"decision_id": decision.id}
        )


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

    touches_wallbit = tool_name not in LOCAL_ONLY_TOOLS
    try:
        return executor(decision, account, args)
    except WallbitUncertainError as exc:
        # The request may have been applied. Do NOT retry, do NOT report it as
        # rejected: freeze the decision and let the reconciliation task settle
        # it against Wallbit's own transaction history.
        logger.error(
            "wallbit write in unknown state",
            exc_info=exc,
            extra={"decision_id": decision.id, "tool": tool_name},
        )
        mark_uncertain(decision, error=str(exc))
        _schedule_reconciliation(decision)
        return {"ok": False, "uncertain": True, "error": str(exc)}
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
    finally:
        if touches_wallbit:
            _dispatch_sync(account.id)
