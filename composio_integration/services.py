"""High-level orchestration for the Composio connect flow.

This module hosts the only place where state transitions on
``ComposioConnection`` happen: views and tasks delegate here so the
state machine stays in one file.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from .client import ConnectRequestDTO, get_client
from .exceptions import (
    ComposioAlreadyConnectedError,
    ComposioPermanentError,
    StateTokenError,
)
from .registry import get_handler
from .state_token import consume_nonce, decode_state, encode_state

if TYPE_CHECKING:
    from rest_framework.request import Request

    from gmailbot.models import ComposioConnection

logger = logging.getLogger(__name__)

User = get_user_model()


def _resolve_backend_url(request: "Request | None") -> str:
    """Return the public base URL the OAuth callback should land on.

    Derived from the incoming ``request`` so el callback siempre apunta al
    host real del backend — funciona igual en local (`localhost:8000`),
    ngrok (`*.ngrok-free.app`) y Railway (`*.up.railway.app`) sin env vars.
    Falla rápido si por algún motivo el caller no nos pasó el request.
    """
    if request is None:
        raise ComposioPermanentError(
            "cannot resolve backend URL: request is required"
        )
    return request.build_absolute_uri("/").rstrip("/")


class ToolkitConnectionService:
    """Orchestrator for connect / callback / disconnect / provision flows.

    The class is stateless; instances exist only so consumers can mock
    or extend a single object instead of patching module-level
    functions.
    """

    def start_connect_flow(
        self,
        user: object,
        toolkit_slug: str,
        request: "Request | None" = None,
    ) -> ConnectRequestDTO:
        """Create or refresh a pending connection and return the redirect URL."""
        from gmailbot.models import ComposioConnection

        auth_configs: dict[str, str] = getattr(settings, "COMPOSIO_AUTH_CONFIGS", {}) or {}
        auth_config_id = auth_configs.get(toolkit_slug, "")
        if not auth_config_id:
            raise ComposioPermanentError(
                f"no auth_config_id configured for toolkit '{toolkit_slug}'"
            )

        with transaction.atomic():
            connection = (
                ComposioConnection.objects.select_for_update()
                .filter(user=user)
                .first()
            )
            if connection is not None:
                connection.status = ComposioConnection.STATUS_PENDING
                connection.last_error = ""
                connection.save(
                    update_fields=["status", "last_error", "updated_at"]
                )
            else:
                connection = ComposioConnection.objects.create(
                    user=user,
                    status=ComposioConnection.STATUS_PENDING,
                    connected_account_id="",
                )

        state = encode_state(user.id, toolkit_slug)
        base_url = _resolve_backend_url(request)
        callback_url = (
            f"{base_url}/api/integrations/{toolkit_slug}/callback/?state={state}"
        )

        client = get_client()
        try:
            dto = client.create_connect_session(
                str(user.id), toolkit_slug, auth_config_id, callback_url
            )
        except ComposioAlreadyConnectedError:
            # Composio ya tiene una conexión activa para este usuario.
            # Sync el row local en vez de bloquear al usuario.
            return self._recover_already_connected(
                user, toolkit_slug, auth_config_id
            )

        # Persist the upstream id so the callback can be cross-checked even
        # before the user finishes the OAuth dance; status stays ``pending``.
        with transaction.atomic():
            persisted = (
                ComposioConnection.objects.select_for_update()
                .filter(user=user)
                .first()
            )
            if persisted is not None:
                persisted.connected_account_id = dto.connected_account_id
                persisted.save(
                    update_fields=["connected_account_id", "updated_at"]
                )

        logger.info(
            "composio connect flow started",
            extra={
                "event": "connect_flow_started",
                "user_id": user.id,
                "toolkit": toolkit_slug,
                "connected_account_id": dto.connected_account_id,
            },
        )
        return dto

    def _recover_already_connected(
        self,
        user: object,
        toolkit_slug: str,
        auth_config_id: str,
    ) -> ConnectRequestDTO:
        """Sincroniza la conexión local cuando Composio reporta ya conectado."""
        from gmailbot.models import ComposioConnection

        existing = get_client().find_active_connected_account(
            str(user.id), auth_config_id
        )
        if existing is None:
            # Composio dijo "ya conectado" pero después no la encuentra:
            # estado inconsistente — pedirle al usuario reintentar.
            raise ComposioPermanentError(
                "Composio reports an active connection but list() returned none"
            )

        with transaction.atomic():
            connection, _created = ComposioConnection.objects.select_for_update().get_or_create(
                user=user,
                defaults={
                    "connected_account_id": existing.id,
                    "status": ComposioConnection.STATUS_ACTIVE,
                    "google_email": existing.profile_email,
                },
            )
            if connection.connected_account_id != existing.id or connection.status != ComposioConnection.STATUS_ACTIVE:
                connection.connected_account_id = existing.id
                connection.status = ComposioConnection.STATUS_ACTIVE
                connection.google_email = existing.profile_email
                connection.last_error = ""
                connection.save(
                    update_fields=[
                        "connected_account_id",
                        "status",
                        "google_email",
                        "last_error",
                        "updated_at",
                    ]
                )

        # Encolar provisioning del trigger por si quedó pendiente.
        from .tasks import provision_triggers_async

        provision_triggers_async.delay(connection.id, toolkit_slug)

        logger.info(
            "composio recovered already-connected state",
            extra={
                "event": "connect_flow_recovered_active",
                "user_id": user.id,
                "toolkit": toolkit_slug,
                "connected_account_id": existing.id,
            },
        )

        # redirect_url vacío señala "no hay flujo OAuth nuevo, ya activa".
        return ConnectRequestDTO(
            connected_account_id=existing.id,
            redirect_url="",
            status="ALREADY_CONNECTED",
        )

    def complete_connect_flow(
        self,
        state_token: str,
        status: str,
        connected_account_id: str,
    ) -> "ComposioConnection":
        """Validate the callback and transition the connection to ``active``."""
        from gmailbot.models import ComposioConnection

        payload = decode_state(state_token)
        if not consume_nonce(payload["nonce"]):
            raise StateTokenError("nonce already consumed")

        uid = int(payload["uid"])
        toolkit_slug = str(payload["tk"])

        if status != "success":
            with transaction.atomic():
                conn = (
                    ComposioConnection.objects.select_for_update()
                    .filter(user_id=uid)
                    .first()
                )
                if conn is not None:
                    conn.status = ComposioConnection.STATUS_FAILED
                    conn.last_error = f"callback status={status}"
                    conn.save(
                        update_fields=["status", "last_error", "updated_at"]
                    )
            raise ComposioPermanentError(f"callback reported status={status}")

        dto = get_client().get_connected_account(connected_account_id)

        if dto.user_id != str(uid):
            # The connected_account does not belong to the user who started
            # the flow. Refuse to bind it to avoid cross-account hijack.
            logger.error(
                "composio callback ownership mismatch",
                extra={
                    "event": "callback_ownership_mismatch",
                    "user_id": uid,
                    "dto_user_id": dto.user_id,
                    "connected_account_id": connected_account_id,
                    "toolkit": toolkit_slug,
                },
            )
            raise ComposioPermanentError("account ownership mismatch")

        with transaction.atomic():
            connection = (
                ComposioConnection.objects.select_for_update()
                .filter(user_id=uid)
                .first()
            )
            if connection is None:
                # The pending row was wiped between connect and callback;
                # recreate it so the user is not stuck.
                connection = ComposioConnection.objects.create(
                    user_id=uid,
                    connected_account_id=connected_account_id,
                    status=ComposioConnection.STATUS_ACTIVE,
                    google_email=dto.profile_email,
                )
            else:
                connection.connected_account_id = connected_account_id
                connection.google_email = dto.profile_email
                connection.status = ComposioConnection.STATUS_ACTIVE
                connection.last_error = ""
                connection.trigger_id = ""
                connection.save(
                    update_fields=[
                        "connected_account_id",
                        "google_email",
                        "status",
                        "last_error",
                        "trigger_id",
                        "updated_at",
                    ]
                )

        try:
            handler = get_handler(toolkit_slug)
        except KeyError:
            logger.error(
                "composio callback: no handler registered",
                extra={
                    "event": "callback_no_handler",
                    "toolkit": toolkit_slug,
                    "user_id": uid,
                },
            )
        else:
            handler.on_connected(connection)

        # Late import to avoid Celery <-> Django app loading races.
        from .tasks import provision_triggers_async

        provision_triggers_async.delay(connection.id, toolkit_slug)

        logger.info(
            "composio connect flow completed",
            extra={
                "event": "connect_flow_completed",
                "user_id": uid,
                "toolkit": toolkit_slug,
                "connected_account_id": connected_account_id,
            },
        )
        return connection

    def disconnect(self, user: object, toolkit_slug: str) -> None:
        """Tear down the connection both locally and upstream in Composio."""
        from gmailbot.models import ComposioConnection

        with transaction.atomic():
            connection = (
                ComposioConnection.objects.select_for_update()
                .filter(user=user)
                .first()
            )
            if connection is None or connection.status == ComposioConnection.STATUS_DISCONNECTED:
                return

            # Run the toolkit-specific cleanup before we tear the row down
            # so handlers can still read local state.
            try:
                handler = get_handler(toolkit_slug)
            except KeyError:
                logger.warning(
                    "composio disconnect: no handler registered",
                    extra={
                        "event": "disconnect_no_handler",
                        "toolkit": toolkit_slug,
                        "user_id": user.id,
                    },
                )
            else:
                try:
                    handler.on_disconnected(connection)
                except Exception:
                    logger.exception(
                        "composio handler on_disconnected raised",
                        extra={
                            "toolkit": toolkit_slug,
                            "user_id": user.id,
                        },
                    )

            client = get_client()

            if connection.trigger_id:
                try:
                    client.delete_trigger(connection.trigger_id)
                except Exception:
                    logger.warning(
                        "composio delete_trigger failed (best-effort)",
                        extra={
                            "event": "disconnect_delete_trigger_failed",
                            "trigger_id": connection.trigger_id,
                            "user_id": user.id,
                        },
                    )

            if connection.connected_account_id:
                try:
                    client.delete_connected_account(connection.connected_account_id)
                except Exception:
                    logger.warning(
                        "composio delete_connected_account failed (best-effort)",
                        extra={
                            "event": "disconnect_delete_account_failed",
                            "connected_account_id": connection.connected_account_id,
                            "user_id": user.id,
                        },
                    )

            connection.status = ComposioConnection.STATUS_DISCONNECTED
            connection.last_error = ""
            connection.save(
                update_fields=["status", "last_error", "updated_at"]
            )

        logger.info(
            "composio connection disconnected",
            extra={
                "event": "connect_flow_disconnected",
                "user_id": user.id,
                "toolkit": toolkit_slug,
            },
        )

    def provision_triggers(self, connection: "ComposioConnection") -> None:
        """Create the triggers declared by the toolkit's registered handler."""
        # TODO: confirmar con plan — ComposioConnection no tiene campo
        # ``toolkit`` aún; mientras solo soportemos Gmail forzamos el slug.
        # Cuando entre Slack agregar columna y leerla aquí.
        toolkit_slug = "gmail"
        handler = get_handler(toolkit_slug)

        specs = list(handler.trigger_specs or [])
        if len(specs) > 1:
            # TODO: confirmar con plan — el modelo persiste un único
            # trigger_id; cuando un toolkit declare múltiples triggers
            # habrá que ampliar el modelo o crear un Trigger separado.
            logger.warning(
                "composio handler declared multiple triggers; only the last id is kept",
                extra={
                    "event": "provision_multiple_triggers",
                    "toolkit": toolkit_slug,
                    "count": len(specs),
                },
            )

        client = get_client()
        last_trigger_id = ""
        for spec in specs:
            slug = str(spec.get("slug", ""))
            if not slug:
                continue
            config = spec.get("config", {}) or {}
            dto = client.create_trigger(
                str(connection.user.id),
                slug,
                config,
                connected_account_id=connection.connected_account_id,
            )
            last_trigger_id = dto.trigger_id

        connection.trigger_id = last_trigger_id
        connection.save(update_fields=["trigger_id", "updated_at"])

        logger.info(
            "composio triggers provisioned",
            extra={
                "event": "triggers_provisioned",
                "toolkit": toolkit_slug,
                "user_id": connection.user_id,
                "trigger_id": last_trigger_id,
                "count": len(specs),
            },
        )
