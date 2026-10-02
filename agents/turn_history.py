"""Turnos en fila por usuario.

Telegram y WhatsApp guardan el mensaje entrante antes de correr el agente. Si
el usuario escribe "12000 gaseosa" y a los cinco segundos "12000 préstamo
amigo", los dos turnos corrían a la vez: el segundo veía la gaseosa sin
respuesta en el historial y la registraba otra vez (2026-10-01, gastos
triplicados en producción).

``user_turn`` pone en fila los turnos de un mismo usuario: el segundo espera a
que el primero termine y guarde su respuesta, y así ve en el historial que la
gaseosa ya quedó registrada. El historial en sí lo arma
``telegrambot.utils.fetch_last_messages`` sin el mensaje actual ni los que aún
esperan turno.

El candado vive en Redis (el broker de Celery, que comparten el proceso web
donde corre Telegram y los workers de WhatsApp), con adquisición atómica,
liberación atómica por token y renovación mientras el turno sigue vivo. Si
Redis falla, el turno sigue sin candado: nunca deja a Tresqu sin responder.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from django.conf import settings

from telegrambot.config import AGENT_EXECUTION_TIMEOUT

logger = logging.getLogger(__name__)

# Vida del candado sin renovar: si el proceso muere con él puesto, caduca solo
# en este tiempo. Mientras el turno sigue vivo se renueva cada tercio.
_LOCK_TTL = 60
# Cuánto espera un turno a que termine el anterior. Cubre el timeout del
# supervisor más la carga de contexto y el guardado, y queda por debajo del
# soft time limit de la tarea de WhatsApp (240 s). Pasado ese tiempo el turno
# sigue igual: el anterior está colgado y no vale la pena dejar mudo a nadie.
_WAIT_SECONDS = int(AGENT_EXECUTION_TIMEOUT) + 30
_POLL_SECONDS = 0.5


def _lock_key(user_id) -> str:
    return f"tresqu:agents:turn:{user_id}"


def _redis_client():
    """Cliente Redis nuevo por turno.

    Los clientes de ``redis.asyncio`` quedan atados al event loop donde se
    crean, y las tareas de WhatsApp abren un loop por mensaje.
    """
    from redis.asyncio import Redis

    return Redis.from_url(
        settings.CELERY_BROKER_URL, socket_timeout=5, socket_connect_timeout=5
    )


async def _keep_alive(lock) -> None:
    while True:
        await asyncio.sleep(_LOCK_TTL / 3)
        try:
            await lock.extend(_LOCK_TTL, replace_ttl=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("turno en fila: no se pudo renovar el candado (%s)", exc)
            return


@asynccontextmanager
async def user_turn(user_id):
    """Ejecuta el bloque cuando ningún otro turno del usuario esté en curso.

    Envuelve el turno completo: cargar historial, correr el agente y guardar
    la respuesta. Si la respuesta se guardara fuera del bloque, el turno
    siguiente podría leer el historial justo antes y no verla.
    """

    client = None
    lock = None
    acquired = False
    try:
        client = _redis_client()
        lock = client.lock(
            _lock_key(user_id),
            timeout=_LOCK_TTL,
            sleep=_POLL_SECONDS,
            blocking_timeout=_WAIT_SECONDS,
            thread_local=False,
        )
        acquired = bool(await lock.acquire())
        if not acquired:
            logger.warning(
                "turno en fila: el usuario %s sigue con un turno en curso tras %ss; se procesa igual",
                user_id, _WAIT_SECONDS,
            )
    except Exception as exc:  # noqa: BLE001 — el candado nunca rompe el flujo
        logger.warning("turno en fila: Redis no disponible (%s); sigue sin candado", exc)

    renewer = asyncio.create_task(_keep_alive(lock)) if acquired else None
    try:
        yield
    finally:
        if renewer:
            renewer.cancel()
            with suppress(asyncio.CancelledError):
                await renewer
        if acquired:
            try:
                await lock.release()
            except Exception as exc:  # noqa: BLE001 — caducó o lo tomó otro: no es nuestro
                logger.warning("turno en fila: no se pudo soltar el candado (%s)", exc)
        if client is not None:
            with suppress(Exception):
                await client.aclose()
