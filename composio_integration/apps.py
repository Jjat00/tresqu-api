"""Django app config for the Composio integration layer.

Initialises the SDK singleton on boot so any missing or invalid
``COMPOSIO_API_KEY`` crashes the process at startup instead of leaking
into the first webhook.
"""

from __future__ import annotations

import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class ComposioIntegrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "composio_integration"
    verbose_name = "Composio Integration"

    def ready(self) -> None:
        # Skip eager init during ``manage.py`` admin commands that don't
        # need the SDK (collectstatic, makemigrations) and when the key
        # is intentionally unset (CI, local dev without Composio).
        if not getattr(settings, "COMPOSIO_API_KEY", ""):
            logger.warning(
                "composio integration: COMPOSIO_API_KEY is empty; client init skipped",
                extra={"event": "client_init_skipped"},
            )
            return

        try:
            from .client import get_client

            get_client()
            logger.info(
                "composio integration: client initialised",
                extra={"event": "client_init_ok"},
            )
        except Exception as exc:
            logger.error(
                "composio integration: client initialisation failed",
                extra={"event": "client_init_failed", "error": str(exc)},
            )
            raise
