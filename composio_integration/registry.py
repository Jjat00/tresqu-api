"""Toolkit registry: per-toolkit code registers a handler at app boot.

The generic views in ``composio_integration.views`` route by ``toolkit``
slug. Each downstream app (``gmailbot`` today, ``slackbot`` tomorrow)
registers a :class:`ToolkitHandler` from its ``apps.ready`` hook.
Keeping this in a separate module avoids circular imports between
``composio_integration`` and the toolkit-specific apps.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .client import ParsedPayload

logger = logging.getLogger(__name__)


@runtime_checkable
class ToolkitHandler(Protocol):
    """Contract every toolkit integration must satisfy.

    ``trigger_specs`` is a list of ``{"slug": str, "config": dict}``
    descriptors used by :func:`services.provision_triggers` to know
    what to create after the user finishes the OAuth flow.
    """

    slug: str
    trigger_specs: list[dict]

    def handle_webhook(self, parsed: "ParsedPayload", connection: object) -> None:
        """Dispatch a verified webhook to the toolkit's processing pipeline."""
        ...

    def on_connected(self, connection: object) -> None:
        """Hook fired after a successful connect callback."""
        ...

    def on_disconnected(self, connection: object) -> None:
        """Hook fired after the user (or webhook) drops the connection."""
        ...


_registry: dict[str, ToolkitHandler] = {}


def register_toolkit(slug: str, handler: ToolkitHandler) -> None:
    """Idempotently register a handler. Overwrites on duplicate ``slug``."""
    if not isinstance(handler, ToolkitHandler):
        raise TypeError(
            f"handler for '{slug}' does not implement ToolkitHandler protocol"
        )
    if slug in _registry:
        logger.warning(
            "composio toolkit handler overridden",
            extra={"event": "toolkit_handler_overridden", "toolkit": slug},
        )
    _registry[slug] = handler
    logger.info(
        "composio toolkit handler registered",
        extra={"event": "toolkit_handler_registered", "toolkit": slug},
    )


def get_handler(slug: str) -> ToolkitHandler:
    """Look up a registered handler; raises ``KeyError`` if absent."""
    try:
        return _registry[slug]
    except KeyError as exc:
        raise KeyError(f"no Composio toolkit handler registered for '{slug}'") from exc


def registered_slugs() -> list[str]:
    """Return all currently registered toolkit slugs (debug/introspection)."""
    return sorted(_registry.keys())


def _reset_for_tests() -> None:
    """Drop the registry; only for use in test fixtures."""
    _registry.clear()
