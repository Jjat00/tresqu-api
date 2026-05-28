"""REST endpoint for asset price history (dashboard chart).

The service already returns a clean, JSON-ready canonical payload, so the
view just validates the range, calls it, and maps the error taxonomy to HTTP
statuses (mirroring the error mapping in ``wallbit.views.PortfolioSummaryView``).
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import (
    MarketDataAuthError,
    MarketDataConfigError,
    MarketDataError,
    MarketDataNotFoundError,
    MarketDataRateLimitError,
)
from .service import VALID_RANGES, get_price_history, get_sparklines

logger = logging.getLogger(__name__)


class AssetPriceHistoryView(APIView):
    """GET /api/market/assets/{symbol}/history/?range=1m

    Returns the canonical price-history payload (points + summary) for the
    given symbol and range. Read-only; no Wallbit account required.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="range",
                description=f"Rango temporal. Uno de: {', '.join(VALID_RANGES)}.",
                required=False,
                type=str,
            )
        ]
    )
    def get(self, request, symbol: str):
        range_ = request.query_params.get("range", "1m").lower()

        try:
            payload = get_price_history(symbol, range_)
        except ValueError:
            return Response(
                {"detail": f"Rango inválido. Usa uno de: {', '.join(VALID_RANGES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except MarketDataNotFoundError as exc:
            return Response(
                {"detail": f"Sin datos de mercado para '{symbol.upper()}'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except MarketDataRateLimitError:
            return Response(
                {"detail": "Límite de datos de mercado alcanzado. Intenta en unos minutos."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except (MarketDataAuthError, MarketDataConfigError) as exc:
            logger.error("marketdata not configured / auth error", exc_info=exc)
            return Response(
                {"detail": "El proveedor de datos de mercado no está disponible."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except MarketDataError as exc:
            logger.warning("marketdata upstream failure", exc_info=exc)
            return Response(
                {"detail": "No pude obtener el histórico de precios ahora mismo."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(payload)


class SparklinesView(APIView):
    """GET /api/market/sparklines/?symbols=AAPL,MSFT&range=1d

    Batch mini-series + change% for a markets table. Degrades gracefully: a
    symbol with no data (or a provider outage) comes back as ``null`` so the
    table still renders the rest from Wallbit data.
    """

    permission_classes = [permissions.IsAuthenticated]

    MAX_SYMBOLS = 30

    @extend_schema(
        parameters=[
            OpenApiParameter(name="symbols", description="Símbolos separados por coma.", required=True, type=str),
            OpenApiParameter(name="range", description=f"Rango. Uno de: {', '.join(VALID_RANGES)}.", required=False, type=str),
        ]
    )
    def get(self, request):
        symbols_raw = request.query_params.get("symbols", "")
        range_ = request.query_params.get("range", "1d").lower()
        symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()][: self.MAX_SYMBOLS]
        if not symbols:
            return Response({"sparklines": {}})

        try:
            data = get_sparklines(symbols, range_)
        except ValueError:
            return Response(
                {"detail": f"Rango inválido. Usa uno de: {', '.join(VALID_RANGES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"sparklines": data})
