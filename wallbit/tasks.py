"""Celery tasks for Wallbit: periodic sync + embedding ingest.

`sync_wallbit_transactions(account_id)` walks the user's recent
Wallbit /transactions, upserts each row into WallbitTxMirror, and
generates a text-embedding-3-small embedding for the RAG history tool.

Scheduled every 15 minutes via celery beat; can also be triggered ad-hoc
from POST /api/wallbit/sync.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from langchain_openai import OpenAIEmbeddings

from .agent_safety import mark_executed, mark_failed
from .client import WallbitClient, WallbitError
from .crypto import decrypt_api_key
from .models import AgentDecision, Investment, WallbitAccount, WallbitTxMirror
from .notify import notify_decision_user

logger = logging.getLogger(__name__)

_embeddings: OpenAIEmbeddings | None = None


def _get_embeddings() -> OpenAIEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = OpenAIEmbeddings(
            api_key=settings.OPENAI_API_KEY,
            model="text-embedding-3-small",
        )
    return _embeddings


def _str_field(value: Any) -> str:
    """Coerce a Wallbit response field to a string.

    Wallbit returns several fields (currency, type, status...) as nested
    objects like ``{"code": "USD", "name": "US Dollar"}``. Falls back to
    common identifying keys before giving up.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("code", "symbol", "name", "value", "id", "ticker"):
            nested = value.get(key)
            if isinstance(nested, str) and nested:
                return nested
            if isinstance(nested, (int, float)):
                return str(nested)
        return ""
    return str(value)


def _tx_text(tx: dict[str, Any]) -> str:
    parts = [
        _str_field(tx.get("type")),
        _str_field(tx.get("status")),
        f"{_str_field(tx.get('source_amount'))} {_str_field(tx.get('source_currency'))}".strip(),
        f"→ {_str_field(tx.get('dest_amount'))} {_str_field(tx.get('dest_currency'))}".strip(),
        _str_field(tx.get("comment")),
        _str_field(tx.get("external_address")),
    ]
    return " | ".join(p for p in parts if p)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, dict):
        for key in ("value", "amount", "raw"):
            nested = value.get(key)
            if nested is not None:
                value = nested
                break
        else:
            return Decimal(0)
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


# Maps Wallbit tx_type to (Investment.kind, Investment.action).
# tx_types not listed here don't represent an investment movement (CARD_PAYMENT,
# DEPOSIT, WITHDRAW, INTERNAL...) and are skipped during backfill.
_TX_TYPE_TO_INVESTMENT: dict[str, tuple[str, str]] = {
    "TRADE": (Investment.STOCK, Investment.BUY),  # BUY/SELL refined below from amounts
    "ROBOADVISOR_DEPOSIT": (Investment.CHEST, Investment.DEPOSIT),
    "ROBOADVISOR_WITHDRAW": (Investment.CHEST, Investment.WITHDRAW),
}


def _backfill_investment(mirror: WallbitTxMirror, tx: dict[str, Any], account: WallbitAccount) -> bool:
    """If `mirror` represents an investment-class tx and there's no Investment
    linked yet, create one. Returns True if a row was created.
    """
    mapping = _TX_TYPE_TO_INVESTMENT.get(mirror.tx_type.upper())
    if mapping is None:
        return False
    if Investment.objects.filter(wallbit_tx=mirror).exists():
        return False

    kind, default_action = mapping
    symbol = ""
    amount_usd = Decimal(0)
    shares: Decimal | None = None

    if mirror.tx_type.upper() == "TRADE":
        # Wallbit TRADE: source_* is what the user paid with, dest_* is what they got.
        # BUY: source=USD, dest=AAPL. SELL: source=AAPL, dest=USD.
        if mirror.dest_currency and mirror.dest_currency.upper() != "USD":
            action = Investment.BUY
            symbol = mirror.dest_currency.upper()
            amount_usd = mirror.source_amount or Decimal(0)
            shares = mirror.dest_amount
        else:
            action = Investment.SELL
            symbol = (mirror.source_currency or "").upper()
            amount_usd = mirror.dest_amount or Decimal(0)
            shares = mirror.source_amount
    else:
        action = default_action
        # ROBOADVISOR_* uses source_amount as the USD delta
        amount_usd = mirror.source_amount or mirror.dest_amount or Decimal(0)

    # Reconcile with an optimistic row the agent executor created at trade time
    # (wallbit_tx still NULL because the mirror didn't exist yet). Adopt it —
    # link the mirror and fill the fields it couldn't know (shares, executed_at)
    # — instead of creating a second row. This is what prevents the agent path
    # and this sync path from duplicating one real Wallbit transaction.
    amount_2dp = (amount_usd or Decimal(0)).quantize(Decimal("0.01"))
    adopt_qs = Investment.objects.filter(
        user=account.user,
        kind=kind,
        action=action,
        amount_usd=amount_2dp,
        wallbit_tx__isnull=True,
    )
    if symbol:
        adopt_qs = adopt_qs.filter(symbol__iexact=symbol)
    optimistic = adopt_qs.order_by("created_at").first()
    if optimistic is not None:
        optimistic.symbol = symbol[:16] or optimistic.symbol
        if shares is not None:
            optimistic.shares = shares
        optimistic.wallbit_tx = mirror
        optimistic.executed_at = mirror.created_at_wallbit
        optimistic.save(update_fields=["symbol", "shares", "wallbit_tx", "executed_at"])
        return False

    Investment.objects.create(
        user=account.user,
        kind=kind,
        action=action,
        symbol=symbol[:16],
        amount_usd=amount_usd,
        shares=shares,
        wallbit_tx=mirror,
        executed_at=mirror.created_at_wallbit,
    )
    return True


def _extract_transactions(payload: Any) -> list[dict[str, Any]]:
    """Pull a list of tx dicts out of whatever shape Wallbit returned."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "transactions", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = _extract_transactions(value)
                if nested:
                    return nested
    return []


def run_wallbit_sync(account_id: int, page_limit: int = 50) -> dict[str, Any]:
    """Synchronous core of the Wallbit sync.

    Used directly by the manual /sync endpoint (so the dashboard can refresh
    on the same request) and wrapped by ``sync_wallbit_transactions`` for the
    Celery beat / fire-and-forget path.
    """
    try:
        account = WallbitAccount.objects.get(id=account_id)
    except WallbitAccount.DoesNotExist:
        logger.warning("wallbit sync: account %s missing", account_id)
        return {"ok": False, "error": "account_missing"}

    if account.status != WallbitAccount.CONNECTED:
        return {"ok": False, "error": f"account_{account.status}"}

    api_key = decrypt_api_key(account.encrypted_api_key)
    upserted = 0
    embeddings_made = 0
    investments_created = 0

    try:
        with WallbitClient(api_key) as client:
            response = client.get(
                "/transactions", params={"page": 1, "limit": page_limit}
            )
    except WallbitError as exc:
        logger.warning("wallbit sync fetch failed for account %s", account_id, exc_info=exc)
        account.last_error = str(exc)[:500]
        account.save(update_fields=["last_error"])
        return {"ok": False, "error": str(exc), "upstream_failed": True}

    items = _extract_transactions(response.data)
    if not items:
        logger.info(
            "wallbit sync: no transactions in response for account %s (payload type=%s)",
            account_id,
            type(response.data).__name__,
        )

    embedder = None
    for tx in items:
        if not isinstance(tx, dict):
            logger.warning(
                "wallbit sync: skipping non-dict tx item type=%s account=%s",
                type(tx).__name__,
                account_id,
            )
            continue
        uuid = tx.get("uuid") or tx.get("id")
        if not uuid:
            continue

        external_address = _str_field(tx.get("external_address"))[:256]
        defaults = {
            "account": account,
            "tx_type": _str_field(tx.get("type"))[:32],
            "status": _str_field(tx.get("status"))[:32],
            "source_currency": _str_field(tx.get("source_currency"))[:8],
            "dest_currency": _str_field(tx.get("dest_currency"))[:8],
            "source_amount": _decimal(tx.get("source_amount")),
            "dest_amount": _decimal(tx.get("dest_amount")),
            "external_address": external_address or None,
            "created_at_wallbit": _parse_dt(tx.get("created_at")) or timezone.now(),
            "comment": _str_field(tx.get("comment")),
            "raw": tx,
        }

        obj, created = WallbitTxMirror.objects.update_or_create(
            wallbit_uuid=str(uuid), defaults=defaults
        )
        upserted += 1

        if obj.embedding is None:
            text = _tx_text(tx)
            if text.strip():
                try:
                    embedder = embedder or _get_embeddings()
                    obj.embedding = embedder.embed_query(text)
                    obj.save(update_fields=["embedding"])
                    embeddings_made += 1
                except Exception as exc:
                    logger.warning(
                        "embedding failed for tx %s: %s", obj.wallbit_uuid, exc
                    )

        try:
            if _backfill_investment(obj, tx, account):
                investments_created += 1
        except Exception as exc:
            logger.warning(
                "investment backfill failed for tx %s: %s", obj.wallbit_uuid, exc
            )

    account.last_sync_at = timezone.now()
    account.last_error = ""
    account.save(update_fields=["last_sync_at", "last_error"])

    return {
        "ok": True,
        "upserted": upserted,
        "embeddings_made": embeddings_made,
        "investments_created": investments_created,
        "account_id": account_id,
    }


@shared_task(bind=True, max_retries=2)
def sync_wallbit_transactions(self, account_id: int, page_limit: int = 50) -> dict[str, Any]:
    """Celery wrapper: runs the sync and retries on upstream failure."""
    result = run_wallbit_sync(account_id, page_limit=page_limit)
    if not result.get("ok") and result.get("upstream_failed"):
        try:
            self.retry(exc=WallbitError(result.get("error", "unknown")), countdown=60)
        except self.MaxRetriesExceededError:
            pass
    return result


@shared_task
def sync_all_connected_accounts() -> dict[str, Any]:
    """Beat entrypoint: dispatch sync per connected account."""
    queued = 0
    for account_id in WallbitAccount.objects.filter(
        status=WallbitAccount.CONNECTED
    ).values_list("id", flat=True):
        sync_wallbit_transactions.delay(account_id)
        queued += 1
    return {"queued": queued}


# --------------------------------------------------------------------------- #
# Reconciliation of UNCERTAIN decisions
#
# A write to Wallbit that got no answer (timeout / 5xx) is frozen as UNCERTAIN
# by ``executors.execute_decision``. It is never resent. This task syncs the
# mirror and looks for the transaction Wallbit would have created, then settles
# the decision to EXECUTED (linking the tx) or FAILED, and tells the user.
# --------------------------------------------------------------------------- #

RECONCILE_MAX_ATTEMPTS = 4
RECONCILE_RETRY_SECONDS = 45
# How far before the confirmation we look: Wallbit's ``created_at`` for a fill
# has been observed a few seconds *before* our webhook log line (clock skew).
RECONCILE_LOOKBACK = timedelta(minutes=3)
# Fees: an API fill has shown source_amount == amount (fee null) and app fills
# amount + ~0.3 %. Accept either.
_AMOUNT_ABS_TOLERANCE = Decimal("0.10")
_AMOUNT_REL_TOLERANCE = Decimal("0.02")


def _amount_matches(observed: Decimal | None, expected: Decimal) -> bool:
    if observed is None:
        return False
    tolerance = max(_AMOUNT_ABS_TOLERANCE, expected * _AMOUNT_REL_TOLERANCE)
    return abs(Decimal(observed) - expected) <= tolerance


_MOVE_TX_TYPES = ("INTERNAL", "INVESTMENT_DEPOSIT", "INVESTMENT_WITHDRAW")
_CHEST_TX_TYPES = {
    "wallbit_deposit_chest": ("ROBOADVISOR_DEPOSIT",),
    "wallbit_withdraw_chest": ("ROBOADVISOR_WITHDRAW",),
}


def find_transaction_for_decision(
    decision: AgentDecision, account: WallbitAccount
) -> WallbitTxMirror | None:
    """Earliest mirrored tx that matches the decision and isn't claimed by another.

    Returns None when the tool leaves no transaction trail (card status) or
    nothing matches yet.
    """
    call = (decision.tools_called or [{}])[0]
    tool = call.get("tool") or ""
    args = call.get("args") or {}
    since = (decision.confirmed_at or decision.created_at) - RECONCILE_LOOKBACK

    claimed = set(
        AgentDecision.objects.exclude(id=decision.id)
        .exclude(wallbit_tx_uuid__isnull=True)
        .exclude(wallbit_tx_uuid="")
        .values_list("wallbit_tx_uuid", flat=True)
    )
    candidates = WallbitTxMirror.objects.filter(
        account=account, created_at_wallbit__gte=since
    ).order_by("created_at_wallbit")

    if tool == "wallbit_place_trade":
        symbol = str(args.get("symbol", "")).upper()
        amount = Decimal(str(args.get("amount_usd", "0")))
        direction = str(args.get("action", "BUY")).upper()
        candidates = candidates.filter(tx_type__iexact="TRADE")
        for tx in candidates:
            if tx.wallbit_uuid in claimed:
                continue
            info = (tx.raw or {}).get("trade_info") or {}
            if str(info.get("direction") or direction).upper() != direction:
                continue
            if direction == "BUY":
                ok = (tx.dest_currency or "").upper() == symbol and _amount_matches(
                    tx.source_amount, amount
                )
            else:
                ok = (tx.source_currency or "").upper() == symbol and _amount_matches(
                    tx.dest_amount, amount
                )
            if ok:
                return tx
        return None

    if tool == "wallbit_move_funds":
        amount = Decimal(str(args.get("amount", "0")))
        for tx in candidates.filter(tx_type__in=_MOVE_TX_TYPES):
            if tx.wallbit_uuid not in claimed and _amount_matches(tx.source_amount, amount):
                return tx
        return None

    if tool in _CHEST_TX_TYPES:
        amount = Decimal(str(args.get("amount_usd", "0")))
        for tx in candidates.filter(tx_type__in=_CHEST_TX_TYPES[tool]):
            if tx.wallbit_uuid not in claimed and (
                _amount_matches(tx.source_amount, amount)
                or _amount_matches(tx.dest_amount, amount)
            ):
                return tx
        return None

    return None


def _decision_summary(decision: AgentDecision) -> str:
    call = (decision.tools_called or [{}])[0]
    return (call.get("preview") or {}).get("summary") or "la operación"


@shared_task(bind=True, max_retries=RECONCILE_MAX_ATTEMPTS)
def reconcile_uncertain_decision(self, decision_id: int) -> dict[str, Any]:
    """Settle an UNCERTAIN decision from Wallbit's transaction history.

    Retries a few times (the fill can take a minute to show up), then gives a
    definitive answer either way and notifies the user on their channel.
    """
    decision = (
        AgentDecision.objects.select_related("user").filter(id=decision_id).first()
    )
    if decision is None or decision.status != AgentDecision.UNCERTAIN:
        return {"ok": True, "skipped": True, "decision_id": decision_id}

    account = WallbitAccount.objects.filter(user_id=decision.user_id).first()
    if account is None:
        mark_failed(decision, error="reconcile: wallbit account missing")
        return {"ok": False, "error": "account_missing"}

    sync = run_wallbit_sync(account.id)
    if not sync.get("ok"):
        logger.warning(
            "reconcile: sync failed for decision %s: %s", decision_id, sync.get("error")
        )

    match = find_transaction_for_decision(decision, account)
    summary = _decision_summary(decision)
    if match is not None:
        mark_executed(decision, wallbit_tx_uuid=match.wallbit_uuid)
        notify_decision_user(
            decision,
            f"✅ Verifiqué en Wallbit: {summary} SÍ se ejecutó aunque no hubo "
            f"respuesta a tiempo. No se repitió.\n\n🧾 Tx: {match.wallbit_uuid}",
        )
        return {"ok": True, "executed": True, "tx": match.wallbit_uuid}

    if self.request.retries < RECONCILE_MAX_ATTEMPTS - 1:
        raise self.retry(countdown=RECONCILE_RETRY_SECONDS)

    tool = (decision.tools_called or [{}])[0].get("tool") or ""
    if tool == "wallbit_set_card_status":
        mark_failed(decision, error="reconcile: no transaction trail for card status")
        notify_decision_user(
            decision,
            "⚠️ Wallbit no respondió al cambio de estado de la tarjeta y no puedo "
            "verificarlo desde aquí. Revisa el estado en la app de Wallbit.",
        )
        return {"ok": False, "unverifiable": True}

    mark_failed(decision, error="reconcile: not found in Wallbit history after retries")
    notify_decision_user(
        decision,
        f"❌ Verifiqué en Wallbit y {summary} NO se ejecutó. "
        "Si quieres, pídemela de nuevo.",
    )
    return {"ok": True, "executed": False}
