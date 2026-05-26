"""Wrapper around the Composio SDK used by every toolkit integration.

The wrapper exists so the rest of the codebase never imports
``composio`` directly. Benefits:

* tests can swap a stub without monkey-patching the SDK,
* SDK version bumps stay isolated here,
* every call gets uniform structured logging and retry semantics.

A module-level singleton is initialised from ``apps.py:ready()`` so the
process fails fast if ``COMPOSIO_API_KEY`` is missing on boot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any

from django.conf import settings
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .exceptions import (
    ComposioAlreadyConnectedError,
    ComposioError,
    ComposioPermanentError,
    ComposioTransientError,
    SignatureError,
)

logger = logging.getLogger(__name__)

_instance: "ComposioClient | None" = None
_instance_lock = Lock()


@dataclass(frozen=True, slots=True)
class ConnectRequestDTO:
    """Result of ``create_connect_session``; redirect the user to redirect_url."""

    connected_account_id: str
    redirect_url: str
    status: str


@dataclass(frozen=True, slots=True)
class ConnectedAccountDTO:
    """Hydrated view of a Composio ``connected_account``."""

    id: str
    user_id: str
    toolkit: str
    status: str
    auth_config_id: str = ""
    profile_email: str = ""


@dataclass(frozen=True, slots=True)
class TriggerDTO:
    """Hydrated view of a Composio trigger subscription."""

    trigger_id: str
    slug: str
    status: str


@dataclass(frozen=True, slots=True)
class ParsedPayload:
    """Normalised webhook payload across V1/V2/V3 wire formats.

    ``metadata`` always exposes the V3-style keys (``trigger_slug``,
    ``connected_account_id``, ``user_id``) regardless of the wire version
    so the rest of the codebase only handles one shape.
    """

    version: str
    type: str
    metadata: dict[str, Any]
    data: dict[str, Any]
    raw: dict[str, Any]


class ComposioClient:
    """Thin wrapper over the official Composio SDK client."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ComposioPermanentError("COMPOSIO_API_KEY is required")
        self._api_key = api_key
        try:
            from composio import Composio
        except ImportError as exc:  # pragma: no cover - install error path
            raise ComposioPermanentError(
                "composio SDK is not installed in this environment"
            ) from exc
        self._sdk = Composio(api_key=api_key)

    # ------------------------------------------------------------------
    # Connect / account lifecycle
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(ComposioTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=4),
        reraise=True,
    )
    def create_connect_session(
        self,
        user_id: str,
        toolkit: str,
        auth_config_id: str,
        callback_url: str,
    ) -> ConnectRequestDTO:
        """Start an OAuth flow and return the URL the user must visit.

        ``auth_config_id`` is the Composio ``ac_xxx`` identifier of the
        auth config created in the dashboard for ``toolkit``.
        """
        if not auth_config_id:
            raise ComposioPermanentError(
                f"missing auth_config_id for toolkit '{toolkit}'; "
                "set COMPOSIO_AUTH_CONFIGS in settings"
            )
        logger.info(
            "composio create_connect_session",
            extra={
                "event": "connect_session_create",
                "toolkit": toolkit,
                "user_id": user_id,
                "auth_config_id": auth_config_id,
            },
        )
        try:
            result = self._sdk.connected_accounts.link(
                user_id=user_id,
                auth_config_id=auth_config_id,
                callback_url=callback_url,
                allow_multiple=False,
            )
        except Exception as exc:
            raise _translate_sdk_error(exc) from exc

        redirect_url = _attr(result, "redirect_url", default=None)
        if not redirect_url:
            raise ComposioPermanentError(
                "Composio link() did not return a redirect_url; "
                "auth scheme may not be redirectable"
            )

        return ConnectRequestDTO(
            connected_account_id=_attr(result, "id"),
            redirect_url=str(redirect_url),
            status=str(_attr(result, "status", default="INITIATED") or "INITIATED"),
        )

    @retry(
        retry=retry_if_exception_type(ComposioTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=4),
        reraise=True,
    )
    def get_connected_account(self, account_id: str) -> ConnectedAccountDTO:
        """Fetch a connected account by id; used to double-check callbacks."""
        logger.info(
            "composio get_connected_account",
            extra={
                "event": "connected_account_get",
                "connected_account_id": account_id,
            },
        )
        try:
            result = self._sdk.connected_accounts.get(nanoid=account_id)
        except Exception as exc:
            raise _translate_sdk_error(exc) from exc

        toolkit = _attr(result, "toolkit", default=None)
        toolkit_slug = (
            _attr(toolkit, "slug", default="") if toolkit is not None else ""
        )

        return ConnectedAccountDTO(
            id=_attr(result, "id"),
            user_id=str(_attr(result, "user_id", default="") or ""),
            toolkit=str(toolkit_slug or ""),
            status=str(_attr(result, "status", default="") or ""),
            auth_config_id=str(_attr(result, "auth_config_id", default="") or ""),
            profile_email=str(
                _attr(result, "profile_email", default="") or ""
            ),
        )

    @retry(
        retry=retry_if_exception_type(ComposioTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=4),
        reraise=True,
    )
    def find_active_connected_account(
        self,
        user_id: str,
        auth_config_id: str,
    ) -> ConnectedAccountDTO | None:
        """Devuelve la connected_account ACTIVE más reciente o ``None``.

        Usada para recuperarse del caso en que Composio ya tiene una
        conexión activa para ``(user_id, auth_config_id)`` y nuestro row
        local está desincronizado.
        """
        try:
            page = self._sdk.connected_accounts.list(
                user_ids=[user_id],
                auth_config_ids=[auth_config_id],
                statuses=["ACTIVE"],
            )
        except Exception as exc:
            raise _translate_sdk_error(exc) from exc

        items = list(getattr(page, "items", []) or [])
        if not items:
            return None
        # Coger la más reciente.
        items.sort(
            key=lambda x: getattr(x, "created_at", None) or "",
            reverse=True,
        )
        chosen = items[0]
        toolkit = _attr(chosen, "toolkit", default=None)
        toolkit_slug = (
            _attr(toolkit, "slug", default="") if toolkit is not None else ""
        )
        return ConnectedAccountDTO(
            id=_attr(chosen, "id"),
            user_id=str(_attr(chosen, "user_id", default="") or ""),
            toolkit=str(toolkit_slug or ""),
            status=str(_attr(chosen, "status", default="") or ""),
            auth_config_id=str(_attr(chosen, "auth_config_id", default="") or ""),
            profile_email=str(_attr(chosen, "profile_email", default="") or ""),
        )

    @retry(
        retry=retry_if_exception_type(ComposioTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=4),
        reraise=True,
    )
    def delete_connected_account(self, account_id: str) -> None:
        """Revoke a connected account; used by GDPR cleanup."""
        logger.info(
            "composio delete_connected_account",
            extra={
                "event": "connected_account_delete",
                "connected_account_id": account_id,
            },
        )
        try:
            self._sdk.connected_accounts.delete(nanoid=account_id)
        except Exception as exc:
            raise _translate_sdk_error(exc) from exc

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(ComposioTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=4),
        reraise=True,
    )
    def create_trigger(
        self,
        user_id: str,
        slug: str,
        config: dict[str, Any] | None = None,
        connected_account_id: str = "",
    ) -> TriggerDTO:
        """Provision a trigger subscription on ``slug``.

        Composio exige ``connected_account_id`` para fijar a qué cuenta
        se ata el trigger (su auto-resolution por ``user_id`` puede
        fallar con 400 ``TriggerInstance_ConnectedAccountIdRequired``).
        ``config`` es la configuración específica del trigger; consultá
        ``triggers.get_type(slug).config`` para ver el schema.
        """
        logger.info(
            "composio create_trigger",
            extra={
                "event": "trigger_create",
                "user_id": user_id,
                "trigger_slug": slug,
                "connected_account_id": connected_account_id,
            },
        )
        try:
            kwargs: dict[str, Any] = {
                "user_id": user_id,
                "trigger_config": config or {},
            }
            if connected_account_id:
                kwargs["connected_account_id"] = connected_account_id
            result = self._sdk.triggers.create(slug, **kwargs)
        except Exception as exc:
            raise _translate_sdk_error(exc) from exc

        trigger_id = (
            _attr(result, "trigger_id", default=None)
            or _attr(result, "id", default=None)
            or ""
        )
        return TriggerDTO(
            trigger_id=str(trigger_id),
            slug=slug,
            status=str(_attr(result, "status", default="active") or "active"),
        )

    @retry(
        retry=retry_if_exception_type(ComposioTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=4),
        reraise=True,
    )
    def delete_trigger(self, trigger_id: str) -> None:
        """Tear down a trigger subscription."""
        logger.info(
            "composio delete_trigger",
            extra={
                "event": "trigger_delete",
                "trigger_id": trigger_id,
            },
        )
        try:
            self._sdk.triggers.delete(trigger_id)
        except Exception as exc:
            raise _translate_sdk_error(exc) from exc

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> ParsedPayload:
        """Verify HMAC and decode the payload in one step.

        Composio sends three signing headers:
        ``webhook-id``, ``webhook-signature``, ``webhook-timestamp``.
        Header lookup is case-insensitive — accept both.

        Not retried: signature failures are permanent, and any
        transport-level issue here is a programming bug, not a network
        hiccup.
        """
        lower = {k.lower(): v for k, v in headers.items()}
        webhook_id = lower.get("webhook-id", "")
        webhook_signature = lower.get("webhook-signature", "")
        webhook_timestamp = lower.get("webhook-timestamp", "")

        if not (webhook_id and webhook_signature and webhook_timestamp):
            logger.warning(
                "composio webhook missing signature headers",
                extra={"event": "webhook_signature_failed"},
            )
            raise SignatureError("missing webhook signing headers")

        body_text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        try:
            result = self._sdk.triggers.verify_webhook(
                id=webhook_id,
                payload=body_text,
                secret=settings.COMPOSIO_WEBHOOK_SECRET,
                signature=webhook_signature,
                timestamp=webhook_timestamp,
            )
        except Exception as exc:
            logger.warning(
                "composio webhook signature failed",
                extra={"event": "webhook_signature_failed"},
            )
            raise SignatureError(str(exc)) from exc

        version = str(_attr(result, "version", default="V3") or "V3")
        normalized = _attr(result, "payload", default={}) or {}
        raw = _attr(result, "raw_payload", default={}) or {}

        # Normalize to V3-style envelope so the rest of the codebase only
        # handles one shape. The SDK already returns the normalized
        # TriggerEvent in ``payload``; we map it to our V3 envelope.
        meta_source = _attr(normalized, "metadata", default={}) or {}
        metadata: dict[str, Any] = {
            "trigger_slug": str(_attr(normalized, "trigger_slug", default="") or ""),
            "trigger_id": str(_attr(normalized, "id", default="") or ""),
            "user_id": str(_attr(normalized, "user_id", default="") or ""),
            "toolkit_slug": str(_attr(normalized, "toolkit_slug", default="") or ""),
        }
        if isinstance(meta_source, dict):
            connected = meta_source.get("connected_account") or {}
            if isinstance(connected, dict):
                metadata.setdefault(
                    "connected_account_id", str(connected.get("id") or "")
                )
                metadata.setdefault(
                    "auth_config_id", str(connected.get("auth_config_id") or "")
                )

        data = _attr(normalized, "payload", default={}) or {}
        if not data:
            data = _attr(normalized, "original_payload", default={}) or {}

        return ParsedPayload(
            version=version,
            type=str(_attr(raw, "type", default="composio.trigger.message")),
            metadata=dict(metadata),
            data=dict(data),
            raw=dict(raw) if isinstance(raw, dict) else {"_raw": raw},
        )


def get_client() -> ComposioClient:
    """Return the process-wide ``ComposioClient`` singleton."""
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is None:
            _instance = ComposioClient(api_key=settings.COMPOSIO_API_KEY)
    return _instance


def reset_client_for_tests() -> None:
    """Drop the singleton; only call from test fixtures."""
    global _instance
    with _instance_lock:
        _instance = None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


_SENTINEL: Any = object()


def _attr(obj: Any, name: str, *, default: Any = _SENTINEL) -> Any:
    """Read ``name`` off a Pydantic model, dataclass, or dict uniformly."""
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
    else:
        value = getattr(obj, name, _SENTINEL)
        if value is not _SENTINEL:
            return value
    if default is _SENTINEL:
        raise ComposioPermanentError(f"missing field '{name}' in SDK response")
    return default


def _translate_sdk_error(exc: BaseException) -> ComposioError:
    """Translate any SDK/HTTP error into our retryable/non-retryable split."""
    name = type(exc).__name__.lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)

    if isinstance(exc, ComposioError):
        return exc

    # SDK exception subtypes we know about
    if "signatureverification" in name or "webhookpayload" in name:
        return SignatureError(str(exc))
    if "multipleconnectedaccounts" in name:
        return ComposioAlreadyConnectedError(str(exc))
    if "timeout" in name:
        return ComposioTransientError(str(exc))
    if "notfound" in name:
        return ComposioPermanentError(str(exc))
    if "invalidparams" in name or "validation" in name:
        return ComposioPermanentError(str(exc))

    if status is not None:
        try:
            status_int = int(status)
        except (TypeError, ValueError):
            status_int = 0
        if status_int == 429 or 500 <= status_int < 600:
            return ComposioTransientError(str(exc))
        if 400 <= status_int < 500:
            return ComposioPermanentError(str(exc))

    if "connect" in name or "network" in name:
        return ComposioTransientError(str(exc))
    if "auth" in name or "permission" in name or "forbidden" in name:
        return ComposioPermanentError(str(exc))

    # Default conservative: treat unknown as transient so we retry once.
    return ComposioTransientError(str(exc))
