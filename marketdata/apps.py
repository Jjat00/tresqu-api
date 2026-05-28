"""Django app config for the market-data layer.

This app owns historical price retrieval for Wallbit-tradeable assets.
Wallbit's public API does not expose price history, so we proxy a swappable
external provider (Twelve Data by default) behind a small caching service.

No eager init: the provider key may be intentionally unset in dev/CI, in
which case the service degrades gracefully (clear errors, no crash).
"""

from __future__ import annotations

from django.apps import AppConfig


class MarketdataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "marketdata"
    verbose_name = "Market Data"
