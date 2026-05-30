"""Read-only tools for the market-analyst subagent.

These connect the analyst to the rest of the agent team without giving it any
write power: current quote + fundamentals (reused from Wallbit), historical
price series (marketdata), the user's effective risk profile, and their
Wallbit portfolio (to compute per-symbol weight). The analyst uses them to
contextualize and EDUCATE — never to advise specific buys/sells.

Factory mirrors ``wallbit.tools.make_wallbit_tools``: it closes over
``user_external_id`` / ``user`` so the LLM never has to pass them. Tools run
synchronously inside the agent, same as the existing Wallbit read tools.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from users.models import User
from wallbit.tools import wallbit_get_asset

logger = logging.getLogger(__name__)

# Synonyms the LLM might emit → canonical ranges accepted by the service.
_RANGE_SYNONYMS = {
    "today": "1d", "1day": "1d", "day": "1d",
    "week": "1w", "1week": "1w", "7d": "1w",
    "month": "1m", "1mo": "1m", "1month": "1m", "30d": "1m",
    "3months": "3m", "quarter": "3m",
    "year": "1y", "1year": "1y", "12m": "1y", "ytd": "1y",
    "5year": "5y", "5years": "5y",
    "all": "max", "todo": "max",
}


def _normalize_range(range_: str) -> str:
    r = (range_ or "").strip().lower()
    return _RANGE_SYNONYMS.get(r, r)


def make_analyst_tools(user_external_id: str, *, user: User | None = None) -> list:
    """Return the analyst's read-only tools, bound to the current user."""

    @tool
    def get_asset_quote(symbol: str) -> dict[str, Any]:
        """Ficha actual de un activo: precio actual, cambio, rango 52 semanas, sector, descripción y dividendos.

        Si el usuario tiene Wallbit conectado, la ficha sale de Wallbit (su
        fuente operativa real); si no, se obtiene de una fuente de mercado
        neutral que NO requiere cuenta. En ambos casos son datos REALES: nunca
        inventes un campo que venga vacío — dilo. ``source`` indica el origen.
        Para la evolución en el tiempo usa get_price_history.
        """
        wb = wallbit_get_asset.invoke({
            "user_external_id": user_external_id,
            "symbol": symbol,
        })
        if isinstance(wb, dict) and wb.get("ok"):
            return {"ok": True, "source": "wallbit", "asset": wb.get("asset")}

        # Sin cuenta Wallbit (o Wallbit no disponible): fuente de mercado neutral.
        from marketdata.exceptions import MarketDataError, MarketDataNotFoundError
        from marketdata.service import get_asset_snapshot

        try:
            snap = get_asset_snapshot(symbol)
        except MarketDataNotFoundError:
            return {"ok": False, "error": "symbol_not_found"}
        except ValueError:
            return {"ok": False, "error": "symbol_required"}
        except MarketDataError as exc:
            logger.warning("analyst get_asset_quote fallback failed", exc_info=exc)
            return {"ok": False, "error": "market_data_unavailable", "detail": str(exc)}
        return {"ok": True, "source": "marketdata", **snap}

    @tool
    def get_price_history(symbol: str, range: str = "1m") -> dict[str, Any]:
        """Resumen de la evolución del precio de un activo en un rango de tiempo.

        Args:
            symbol: Símbolo del activo (ej. "NVDA", "VOO").
            range: Rango temporal. Uno de: 1d, 1w, 1m, 3m, 1y, 5y, max.

        Devuelve un resumen compacto (precio actual, cambio absoluto y %, máximo,
        mínimo y tendencia) — úsalo para responder "¿cómo va NVDA este mes?".
        No incluye toda la serie de puntos para no saturar el contexto.
        """
        from marketdata.service import get_price_history as _svc

        canonical = _normalize_range(range)
        try:
            payload = _svc(symbol, canonical)
        except ValueError:
            from marketdata.service import VALID_RANGES
            return {
                "ok": False,
                "error": "invalid_range",
                "valid_ranges": list(VALID_RANGES),
            }
        except Exception as exc:  # MarketDataError y derivados
            logger.warning("analyst get_price_history failed", exc_info=exc)
            return {"ok": False, "error": "market_data_unavailable", "detail": str(exc)}

        return {
            "ok": True,
            "symbol": payload["symbol"],
            "range": payload["range"],
            "summary": payload["summary"],
            "stale": payload.get("stale", False),
        }

    @tool
    def get_user_risk_profile() -> dict[str, Any]:
        """Perfil de riesgo EFECTIVO del usuario (tolerancia, score, fuente, advertencia).

        Úsalo para enmarcar la información de un activo según el apetito de
        riesgo del usuario (sin recomendar comprar/vender).
        """
        resolved = user or _resolve_user(user_external_id)
        if resolved is None:
            return {"ok": False, "error": "user_not_found"}

        from agents.effective_profile import get_effective_profile

        eff = get_effective_profile(resolved, refresh_inference=False)
        return {
            "ok": True,
            "tolerance": eff.tolerance,
            "score": eff.score,
            "confidence": round(eff.confidence, 2),
            "source": eff.source,
            "warning": eff.warning,
        }

    @tool
    def get_user_portfolio() -> dict[str, Any]:
        """Portafolio Wallbit del usuario: peso (%) y ganancia/pérdida por posición.

        Úsalo para contextualizar ("esta acción ya pesa X% de tu portafolio",
        "vas +Y% en NVDA"). Trae por símbolo el número EXACTO de acciones (con
        todos los decimales), precio promedio, valor actual, invertido y P&L en
        USD y %, todo calculado en el backend — NO lo recalcules ni redondees.
        Devuelve connected=false si el usuario no tiene Wallbit conectado.
        """
        resolved = user or _resolve_user(user_external_id)
        if resolved is None:
            return {"ok": False, "error": "user_not_found"}

        from decimal import Decimal

        from wallbit.portfolio import safe_get_holdings, safe_get_summary

        summary = safe_get_summary(resolved)
        if summary is None:
            return {"ok": True, "connected": False}

        holdings = safe_get_holdings(resolved)
        total = sum((h.market_value for h in holdings), Decimal(0))
        positions = []
        for h in holdings:
            weight = float(h.market_value / total * 100) if total > 0 else 0.0
            positions.append({
                "symbol": h.symbol,
                "name": h.name,
                "kind": h.kind,
                "shares": format(h.shares, "f"),
                "avg_cost_usd": str(h.avg_cost.quantize(Decimal("0.01"))),
                "current_price_usd": str(h.current_price.quantize(Decimal("0.01"))),
                "invested_usd": str(h.cost_basis.quantize(Decimal("0.01"))),
                "current_value_usd": str(h.market_value.quantize(Decimal("0.01"))),
                "pnl_usd": str(h.pnl_usd.quantize(Decimal("0.01"))),
                "pnl_pct": round(h.pnl_pct, 2),
                "weight_pct": round(weight, 2),
            })
        return {
            "ok": True,
            "connected": True,
            "total_value_usd": str(total.quantize(Decimal("0.01"))),
            "holdings_count": summary.holdings_count,
            "holdings": positions,
        }

    return [get_asset_quote, get_price_history, get_user_risk_profile, get_user_portfolio]


def _resolve_user(user_external_id: str) -> User | None:
    try:
        return User.objects.get(external_id=user_external_id)
    except User.DoesNotExist:
        return None
