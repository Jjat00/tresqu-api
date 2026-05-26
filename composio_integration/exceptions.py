"""Exception hierarchy for the Composio integration layer.

Split into transient vs permanent so that ``tenacity``/Celery retry
policies can target only the recoverable cases without re-hitting auth
errors or signature failures.
"""

from __future__ import annotations


class ComposioError(Exception):
    """Base class for any failure inside ``composio_integration``."""


class ComposioTransientError(ComposioError):
    """Retriable upstream failure (5xx, timeout, rate-limit)."""


class ComposioPermanentError(ComposioError):
    """Non-retriable upstream failure (4xx, invalid credentials)."""


class ComposioAlreadyConnectedError(ComposioError):
    """El usuario ya tiene una connected_account ACTIVE para el auth_config.

    No es un error real: típicamente el flujo previo se cerró del lado de
    Composio pero el row local quedó stale. Los callers deben recuperarse
    sincronizando el estado local en vez de reintentar."""


class SignatureError(ComposioError):
    """Webhook HMAC verification failed; reject the request."""


class StateTokenError(ComposioError):
    """Callback state token invalid, expired, or replayed."""
