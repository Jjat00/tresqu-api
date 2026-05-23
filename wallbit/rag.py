"""RAG over the user's financial history (expenses + incomes + Wallbit txs).

`query_history(user, query, limit)` embeds the query once and runs three
cosine-similarity searches across the three pgvector tables, then merges
and ranks by distance so the agent gets a single, sortable list.

The embedding model matches what each model was indexed with:
text-embedding-3-small (1536d).
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from langchain_openai import OpenAIEmbeddings
from pgvector.django import CosineDistance

from expenses.models import Expense
from income.models import Income
from users.models import User

from .models import WallbitTxMirror

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


def _expense_item(e: Expense, distance: float) -> dict[str, Any]:
    return {
        "source": "expense",
        "id": e.id,
        "amount": str(e.amount),
        "currency": e.currency,
        "description": e.description or "",
        "category": str(e.user_expense_category or e.category or e.category_str or ""),
        "date": e.timestamp.isoformat() if getattr(e, "timestamp", None) else None,
        "distance": float(distance),
    }


def _income_item(i: Income, distance: float) -> dict[str, Any]:
    return {
        "source": "income",
        "id": i.id,
        "amount": str(i.amount),
        "currency": i.currency,
        "description": i.description or "",
        "date": i.timestamp.isoformat() if getattr(i, "timestamp", None) else None,
        "distance": float(distance),
    }


def _tx_item(tx: WallbitTxMirror, distance: float) -> dict[str, Any]:
    return {
        "source": "wallbit_tx",
        "id": tx.id,
        "wallbit_uuid": tx.wallbit_uuid,
        "tx_type": tx.tx_type,
        "status": tx.status,
        "source_amount": str(tx.source_amount),
        "source_currency": tx.source_currency,
        "dest_amount": str(tx.dest_amount),
        "dest_currency": tx.dest_currency,
        "date": tx.created_at_wallbit.isoformat() if tx.created_at_wallbit else None,
        "comment": tx.comment or "",
        "distance": float(distance),
    }


def query_history(user: User, query: str, limit: int = 10) -> dict[str, Any]:
    text = (query or "").strip()
    if not text:
        return {"ok": False, "error": "query_required"}

    try:
        vector = _get_embeddings().embed_query(text)
    except Exception as exc:
        logger.warning("rag embedding failed: %s", exc)
        return {"ok": False, "error": "embedding_failed", "message": str(exc)}

    per_source_limit = max(3, min(limit, 25))

    expenses = (
        Expense.objects.filter(user=user, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:per_source_limit]
    )
    incomes = (
        Income.objects.filter(user=user, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:per_source_limit]
    )
    txs = (
        WallbitTxMirror.objects.filter(
            account__user=user, embedding__isnull=False
        )
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:per_source_limit]
    )

    combined: list[dict[str, Any]] = []
    combined.extend(_expense_item(e, e.distance) for e in expenses)
    combined.extend(_income_item(i, i.distance) for i in incomes)
    combined.extend(_tx_item(tx, tx.distance) for tx in txs)
    combined.sort(key=lambda r: r["distance"])
    combined = combined[:limit]

    return {
        "ok": True,
        "query": text,
        "count": len(combined),
        "results": combined,
    }
