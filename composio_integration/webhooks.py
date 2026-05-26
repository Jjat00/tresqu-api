"""Pure functions for verifying and normalising Composio webhooks.

Kept as module-level functions (no class) because they are stateless;
the heavy lifting (HMAC + payload decode) is delegated to the SDK via
``ComposioClient.verify_webhook``. This module only adds normalisation
across V1/V2/V3 payload shapes and a thin facade so callers don't need
to know about the client singleton.
"""

from __future__ import annotations

import logging
from typing import Any

from .client import ParsedPayload, get_client
from .exceptions import SignatureError

logger = logging.getLogger(__name__)


def verify_and_parse(
    headers: dict[str, str],
    body: bytes,
    secret: str | None = None,
) -> ParsedPayload:
    """Verify the HMAC signature and return a normalised payload.

    ``secret`` is accepted for symmetry but the SDK reads it from
    settings inside ``ComposioClient``. Raises :class:`SignatureError`
    on any verification failure so the caller can return 401 without
    leaking payload contents.
    """
    client = get_client()
    try:
        parsed = client.verify_webhook(headers=headers, body=body)
    except SignatureError:
        # Re-raise after logging at warning level (no payload).
        logger.warning(
            "composio webhook rejected",
            extra={"event": "webhook_signature_failed"},
        )
        raise

    logger.info(
        "composio webhook accepted",
        extra={
            "event": "webhook_received",
            "version": parsed.version,
            "type": parsed.type,
            "trigger_slug": parsed.metadata.get("trigger_slug")
            or parsed.metadata.get("triggerSlug"),
            "connected_account_id": parsed.metadata.get("connected_account_id")
            or parsed.metadata.get("connectedAccountId"),
            "user_id": parsed.metadata.get("user_id") or parsed.metadata.get("userId"),
        },
    )
    return parsed


def extract_connected_account_id(parsed: ParsedPayload) -> str | None:
    """Pull the ``connected_account_id`` out of either snake or camel case."""
    metadata: dict[str, Any] = parsed.metadata
    return (
        metadata.get("connected_account_id")
        or metadata.get("connectedAccountId")
        or metadata.get("account_id")
    )


def extract_user_id(parsed: ParsedPayload) -> str | None:
    """Pull the ``user_id`` (Composio-side) out of the payload metadata."""
    metadata: dict[str, Any] = parsed.metadata
    return metadata.get("user_id") or metadata.get("userId")


def extract_trigger_slug(parsed: ParsedPayload) -> str | None:
    """Pull the trigger slug; falls back to ``type`` for V1 payloads."""
    metadata: dict[str, Any] = parsed.metadata
    return (
        metadata.get("trigger_slug")
        or metadata.get("triggerSlug")
        or parsed.type
        or None
    )
