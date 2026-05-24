"""Celery tasks for Wallbit: periodic sync + embedding ingest.

`sync_wallbit_transactions(account_id)` walks the user's recent
Wallbit /transactions, upserts each row into WallbitTxMirror, and
generates a text-embedding-3-small embedding for the RAG history tool.

Scheduled every 15 minutes via celery beat; can also be triggered ad-hoc
from POST /api/wallbit/sync.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from langchain_openai import OpenAIEmbeddings

from .client import WallbitClient, WallbitError
from .crypto import decrypt_api_key
from .models import WallbitAccount, WallbitTxMirror

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


@shared_task(bind=True, max_retries=2)
def sync_wallbit_transactions(self, account_id: int, page_limit: int = 50) -> dict[str, Any]:
    """Pull the latest page of /transactions and upsert into the local mirror."""
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

    try:
        with WallbitClient(api_key) as client:
            response = client.get(
                "/transactions", params={"page": 1, "limit": page_limit}
            )
    except WallbitError as exc:
        logger.warning("wallbit sync fetch failed for account %s", account_id, exc_info=exc)
        account.last_error = str(exc)[:500]
        account.save(update_fields=["last_error"])
        try:
            self.retry(exc=exc, countdown=60)
        except self.MaxRetriesExceededError:
            pass
        return {"ok": False, "error": str(exc)}

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

    account.last_sync_at = timezone.now()
    account.last_error = ""
    account.save(update_fields=["last_sync_at", "last_error"])

    return {
        "ok": True,
        "upserted": upserted,
        "embeddings_made": embeddings_made,
        "account_id": account_id,
    }


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
