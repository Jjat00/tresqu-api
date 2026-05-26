"""Signed short-lived state token used to correlate OAuth callbacks.

We need to prove that whoever lands on
``/api/integrations/<toolkit>/callback/`` actually initiated the flow
from ``/connect-url/``: the ``connected_account_id`` arriving in the
query string is spoofable on its own. The state token is a JWT
(HS256, ``SECRET_KEY``) with a 10-minute TTL and a single-use nonce
guarded by the Django cache.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

import jwt
from django.conf import settings
from django.core.cache import cache

from .exceptions import StateTokenError

logger = logging.getLogger(__name__)

_ALG = "HS256"
_TTL_SECONDS = 600  # 10 minutes
_NONCE_CACHE_PREFIX = "composio:nonce:"


def encode_state(user_id: int, toolkit: str) -> str:
    """Mint a fresh state token for ``user_id`` connecting ``toolkit``."""
    payload = {
        "uid": int(user_id),
        "tk": toolkit,
        "exp": int(time.time()) + _TTL_SECONDS,
        "nonce": secrets.token_urlsafe(8),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALG)
    logger.debug(
        "composio state token issued",
        extra={
            "event": "state_token_issued",
            "user_id": user_id,
            "toolkit": toolkit,
        },
    )
    return token


def decode_state(token: str) -> dict[str, Any]:
    """Verify signature + expiry; return the payload.

    Does NOT consume the nonce. Callers must invoke
    :func:`consume_nonce` immediately after this to guarantee
    single-use semantics.
    """
    if not token:
        raise StateTokenError("missing state token")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALG])
    except jwt.ExpiredSignatureError as exc:
        raise StateTokenError("state token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise StateTokenError("state token invalid") from exc

    for field in ("uid", "tk", "nonce"):
        if field not in payload:
            raise StateTokenError(f"state token missing '{field}'")
    return payload


def consume_nonce(nonce: str) -> bool:
    """Mark ``nonce`` as used. Returns ``False`` if it was already seen.

    Implemented via ``cache.add`` which is atomic across processes when
    the configured cache backend is shared (Redis in prod, DB in dev).
    """
    if not nonce:
        return False
    key = f"{_NONCE_CACHE_PREFIX}{nonce}"
    # ``add`` returns True only when the key did not already exist.
    return bool(cache.add(key, True, timeout=_TTL_SECONDS))
