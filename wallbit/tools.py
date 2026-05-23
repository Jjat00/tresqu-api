"""LangChain tools for the Wallbit integration.

All tools accept ``user_external_id`` so the agent prompt (which already
injects this argument for the existing Tresqu tools) can drive them in the
same way.

Read tools return dictionaries that are safe to serialize back to the LLM
context (no secrets, no httpx objects). Write tools live in their own module
and require the confirmation flow described in
``docs/WALLBIT_INTEGRATION.md`` section 7.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from users.models import User

from .client import WallbitClient, WallbitError
from .crypto import decrypt_api_key
from .models import WallbitAccount

logger = logging.getLogger(__name__)


def _load_client(user_external_id: str) -> WallbitClient:
    user = User.objects.get(external_id=user_external_id)
    account = WallbitAccount.objects.get(user=user)
    if account.status != WallbitAccount.CONNECTED:
        raise WallbitError(f"Wallbit account is {account.status}")
    return WallbitClient(decrypt_api_key(account.encrypted_api_key))


def _error_payload(exc: Exception) -> dict[str, Any]:
    status = getattr(exc, "status", None)
    return {"ok": False, "error": str(exc), "status": status}


@tool
def wallbit_get_balance(user_external_id: str) -> dict[str, Any]:
    """Devuelve el saldo Wallbit del usuario: efectivo por moneda y acciones por símbolo.

    Combina GET /balance/checking (saldos en moneda) y GET /balance/stocks
    (posiciones por símbolo, sin precio actual ni P&L). Útil para responder
    "¿cuál es mi saldo?", "¿cuánto tengo en Wallbit?", "¿qué acciones tengo?".
    """
    try:
        with _load_client(user_external_id) as client:
            checking = client.get("/balance/checking").data or {}
            stocks = client.get("/balance/stocks").data or {}
    except WallbitAccount.DoesNotExist:
        return {"ok": False, "error": "wallbit_not_connected"}
    except User.DoesNotExist:
        return {"ok": False, "error": "user_not_found"}
    except WallbitError as exc:
        logger.warning("wallbit_get_balance failed", exc_info=exc)
        return _error_payload(exc)

    return {
        "ok": True,
        "checking": checking.get("data", checking) if isinstance(checking, dict) else checking,
        "stocks": stocks.get("data", stocks) if isinstance(stocks, dict) else stocks,
    }


@tool
def wallbit_list_transactions(
    user_external_id: str,
    limit: int = 10,
    tx_type: str = "",
    from_date: str = "",
    to_date: str = "",
) -> dict[str, Any]:
    """Lista transacciones Wallbit del usuario, ordenadas de más reciente a más antigua.

    Args:
        user_external_id: ID externo del usuario en Tresqu.
        limit: Número de tx a devolver (10|20|50). Default 10.
        tx_type: Filtra por tipo (TRADE, INTERNAL, DEPOSIT, WITHDRAW,
            ROBOADVISOR_DEPOSIT, ROBOADVISOR_WITHDRAW, CARD_PAYMENT). Vacío = todas.
        from_date: YYYY-MM-DD. Opcional.
        to_date: YYYY-MM-DD. Opcional.
    """
    if limit not in (10, 20, 50):
        limit = 10
    params: dict[str, Any] = {"page": 1, "limit": limit}
    if tx_type:
        params["type"] = tx_type
    if from_date:
        params["from_date"] = from_date
    if to_date:
        params["to_date"] = to_date

    try:
        with _load_client(user_external_id) as client:
            response = client.get("/transactions", params=params)
    except WallbitAccount.DoesNotExist:
        return {"ok": False, "error": "wallbit_not_connected"}
    except User.DoesNotExist:
        return {"ok": False, "error": "user_not_found"}
    except WallbitError as exc:
        logger.warning("wallbit_list_transactions failed", exc_info=exc)
        return _error_payload(exc)

    payload = response.data or {}
    return {
        "ok": True,
        "transactions": payload.get("data", []),
        "count": payload.get("count"),
        "pages": payload.get("pages"),
    }


@tool
def wallbit_search_assets(
    user_external_id: str,
    query: str = "",
    category: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Busca activos en el catálogo Wallbit (acciones, ETFs, bonos).

    Args:
        user_external_id: ID externo del usuario en Tresqu.
        query: Texto libre para buscar (símbolo o nombre). Ej "AAPL", "vanguard".
        category: Una de MOST_POPULAR, ETF, DIVIDENDS, TECHNOLOGY, HEALTH,
            CONSUMER_GOODS, ENERGY_AND_WATER, FINANCE, REAL_ESTATE,
            TREASURY_BILLS, VIDEOGAMES, ARGENTINA_ADR. Vacío = sin filtro.
        limit: Máximo de resultados.
    """
    params: dict[str, Any] = {"page": 1, "limit": max(1, min(limit, 50))}
    if query:
        params["search"] = query
    if category:
        params["category"] = category

    try:
        with _load_client(user_external_id) as client:
            response = client.get("/assets", params=params)
    except WallbitAccount.DoesNotExist:
        return {"ok": False, "error": "wallbit_not_connected"}
    except User.DoesNotExist:
        return {"ok": False, "error": "user_not_found"}
    except WallbitError as exc:
        logger.warning("wallbit_search_assets failed", exc_info=exc)
        return _error_payload(exc)

    payload = response.data or {}
    items = payload.get("data", payload if isinstance(payload, list) else [])
    return {"ok": True, "assets": items}


@tool
def wallbit_get_asset(user_external_id: str, symbol: str) -> dict[str, Any]:
    """Devuelve la ficha completa de un activo Wallbit por su símbolo.

    Incluye precio actual, sector, tipo (Stock/ETF/Bond), dividendos cuando
    aplique y descripción. Útil antes de proponer una compra para validar
    que el símbolo existe y mostrar el precio al usuario.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {"ok": False, "error": "symbol_required"}

    try:
        with _load_client(user_external_id) as client:
            response = client.get(f"/assets/{symbol}")
    except WallbitAccount.DoesNotExist:
        return {"ok": False, "error": "wallbit_not_connected"}
    except User.DoesNotExist:
        return {"ok": False, "error": "user_not_found"}
    except WallbitError as exc:
        logger.warning("wallbit_get_asset failed", exc_info=exc, extra={"symbol": symbol})
        return _error_payload(exc)

    payload = response.data or {}
    asset = payload.get("data", payload)
    return {"ok": True, "asset": asset}


WALLBIT_TOOLS = [
    wallbit_get_balance,
    wallbit_list_transactions,
    wallbit_search_assets,
    wallbit_get_asset,
]


def make_wallbit_tools(
    user_external_id: str,
    *,
    channel: str = "whatsapp",
    user_message: str = "",
) -> list:
    """Bind ``user_external_id`` so the LLM doesn't have to provide it.

    The bot wrappers already inject the resolved Tresqu user id into other
    tools the same way (see whatsappbot/services.py and telegrambot/services.py).

    When ``channel`` and ``user_message`` are provided, the write tools and
    the RAG history tool are also returned. Read-only callers can omit them.
    """

    @tool
    def wallbit_get_balance_for_user() -> dict[str, Any]:
        """Devuelve el saldo Wallbit del usuario actual: efectivo por moneda y acciones por símbolo."""
        return wallbit_get_balance.invoke({"user_external_id": user_external_id})

    @tool
    def wallbit_list_transactions_for_user(
        limit: int = 10,
        tx_type: str = "",
        from_date: str = "",
        to_date: str = "",
    ) -> dict[str, Any]:
        """Lista transacciones Wallbit del usuario actual (más recientes primero).

        Args:
            limit: 10 | 20 | 50.
            tx_type: TRADE, INTERNAL, DEPOSIT, WITHDRAW, ROBOADVISOR_DEPOSIT,
                ROBOADVISOR_WITHDRAW, CARD_PAYMENT. Vacío = todas.
            from_date: YYYY-MM-DD opcional.
            to_date: YYYY-MM-DD opcional.
        """
        return wallbit_list_transactions.invoke({
            "user_external_id": user_external_id,
            "limit": limit,
            "tx_type": tx_type,
            "from_date": from_date,
            "to_date": to_date,
        })

    @tool
    def wallbit_search_assets_for_user(
        query: str = "",
        category: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Busca activos en el catálogo Wallbit (acciones, ETFs, bonos)."""
        return wallbit_search_assets.invoke({
            "user_external_id": user_external_id,
            "query": query,
            "category": category,
            "limit": limit,
        })

    @tool
    def wallbit_get_asset_for_user(symbol: str) -> dict[str, Any]:
        """Devuelve la ficha completa de un activo Wallbit por su símbolo."""
        return wallbit_get_asset.invoke({
            "user_external_id": user_external_id,
            "symbol": symbol,
        })

    @tool
    def tresqu_query_history(query: str, limit: int = 10) -> dict[str, Any]:
        """Busca en el historial financiero del usuario (gastos, ingresos y transacciones Wallbit) por similitud semántica.

        Útil para "¿cuánto gasté en restaurantes?", "¿he comprado AAPL antes?",
        "ingresos por freelance del último año". Devuelve los registros más
        relevantes ordenados por similitud.

        Args:
            query: Texto libre describiendo qué buscas.
            limit: Máximo de resultados (default 10).
        """
        try:
            user = User.objects.get(external_id=user_external_id)
        except User.DoesNotExist:
            return {"ok": False, "error": "user_not_found"}

        from .rag import query_history
        return query_history(user, query, limit=limit)

    bound = [
        wallbit_get_balance_for_user,
        wallbit_list_transactions_for_user,
        wallbit_search_assets_for_user,
        wallbit_get_asset_for_user,
        tresqu_query_history,
    ]

    if channel and user_message is not None:
        try:
            user_obj = User.objects.get(external_id=user_external_id)
        except User.DoesNotExist:
            return bound
        from .write_tools import make_wallbit_write_tools
        bound.extend(make_wallbit_write_tools(user_obj, channel, user_message))

    return bound
