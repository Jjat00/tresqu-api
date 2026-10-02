"""Turnos en fila por usuario.

Telegram y WhatsApp guardan el mensaje entrante antes de correr el agente. Si
el usuario escribe "12000 gaseosa" y a los cinco segundos "12000 préstamo
amigo", los dos turnos corrían a la vez: el segundo veía la gaseosa sin
respuesta en el historial y la registraba otra vez (2026-10-01, gastos
triplicados en producción).

``user_turn`` pone en fila los turnos de un mismo usuario, en orden de llegada
(el id del mensaje): el segundo espera a que el primero termine y guarde su
respuesta, y así ve en el historial que la gaseosa ya quedó registrada. El historial en sí lo arma
``telegrambot.utils.fetch_last_messages`` sin el mensaje actual ni los que aún
esperan turno.

La fila y el candado viven en Redis (el broker de Celery, que comparten el
proceso web donde corre Telegram y los workers de WhatsApp): una fila ordenada
(ZSET) por el id del mensaje, con un latido por turno para descartar a los que
murieron, y un candado con adquisición y liberación atómicas por token que se
renueva mientras el turno sigue vivo. Si
Redis falla, el turno sigue sin candado: nunca deja a Tresqu sin responder.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
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


def _queue_key(user_id) -> str:
    return f"tresqu:agents:queue:{user_id}"


def _alive_key(user_id, token: str) -> str:
    return f"tresqu:agents:alive:{user_id}:{token}"


# Puesto en la fila de un turno sin mensaje guardado: después de todos los que
# sí tienen id, y entre ellos por hora de llegada.
_NO_ORDER = 10**15


async def _is_head(client, user_id, token: str) -> bool:
    """``True`` si ``token`` es el primero vivo de la fila.

    La fila va ordenada por el id del mensaje entrante, que es el orden real de
    llegada. Los turnos que murieron sin salir de la fila se reconocen porque
    su latido caducó, y se sacan.
    """

    queue = _queue_key(user_id)
    rank = await client.zrank(queue, token)
    if rank is None or rank == 0:
        return True
    alive_ahead = False
    for member in await client.zrange(queue, 0, rank - 1):
        member = member.decode() if isinstance(member, bytes) else member
        if await client.exists(_alive_key(user_id, member)):
            alive_ahead = True
        else:
            await client.zrem(queue, member)
    return not alive_ahead


async def _acquire_in_order(client, user_id, token: str, lock, deadline: float) -> bool:
    """Toma el candado solo siendo la cabeza de la fila. ``False`` si se agota.

    Un candado solo no basta: quien despierta primero de su espera se lo lleva,
    y un tercer mensaje podía adelantarse al segundo. Por eso la cabeza se
    vuelve a comprobar en cada intento (un id menor puede entrar tarde a la
    fila) y el latido se renueva durante toda la espera (si no, un turno vivo
    que espera mucho parecería muerto y lo sacarían de la fila).
    """

    loop = asyncio.get_running_loop()
    while True:
        await client.set(_alive_key(user_id, token), 1, ex=_LOCK_TTL)
        if await _is_head(client, user_id, token) and await lock.acquire(blocking=False):
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(_POLL_SECONDS)


async def _keep_alive(client, user_id, token: str, lock) -> None:
    while True:
        await asyncio.sleep(_LOCK_TTL / 3)
        try:
            await client.set(_alive_key(user_id, token), 1, ex=_LOCK_TTL)
            await lock.extend(_LOCK_TTL, replace_ttl=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("turno en fila: no se pudo renovar el candado (%s)", exc)
            return


@asynccontextmanager
async def user_turn(user_id, order=None):
    """Ejecuta el bloque en orden de llegada y sin otro turno del usuario en curso.

    ``order`` es el id del mensaje entrante ya guardado: fija el puesto en la
    fila. Envuelve el turno completo: cargar historial, correr el agente y
    guardar la respuesta. Si la respuesta se guardara fuera del bloque, el
    turno siguiente podría leer el historial justo antes y no verla.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _WAIT_SECONDS
    token = uuid.uuid4().hex
    client = None
    lock = None
    queued = False
    acquired = False
    try:
        client = _redis_client()
        queue = _queue_key(user_id)
        await client.set(_alive_key(user_id, token), 1, ex=_LOCK_TTL)
        await client.zadd(queue, {token: order if order is not None else _NO_ORDER + time.time()})
        await client.expire(queue, _WAIT_SECONDS + _LOCK_TTL)
        queued = True
        # La fila ordena; el candado garantiza la exclusión aunque un turno
        # entre tarde a la fila con un id menor que el que ya está corriendo.
        lock = client.lock(
            _lock_key(user_id),
            timeout=_LOCK_TTL,
            sleep=_POLL_SECONDS,
            thread_local=False,
        )
        acquired = await _acquire_in_order(client, user_id, token, lock, deadline)
        if not acquired:
            logger.warning(
                "turno en fila: el usuario %s sigue con un turno en curso tras %ss; se procesa igual",
                user_id, _WAIT_SECONDS,
            )
    except Exception as exc:  # noqa: BLE001 — la fila nunca rompe el flujo
        logger.warning("turno en fila: Redis no disponible (%s); sigue sin candado", exc)

    renewer = (
        asyncio.create_task(_keep_alive(client, user_id, token, lock)) if acquired else None
    )
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
        if queued:
            with suppress(Exception):
                await client.zrem(_queue_key(user_id), token)
                await client.delete(_alive_key(user_id, token))
        if client is not None:
            with suppress(Exception):
                await client.aclose()
