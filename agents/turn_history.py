"""Historial limpio y turnos en fila por usuario.

Telegram y WhatsApp guardan el mensaje entrante en ``users_message`` ANTES de
cargar el historial, así que ese mensaje llegaba dos veces al modelo: en el
historial y como turno actual. Efectos vistos en producción (2026-10-01):

- El supervisor registraba cada gasto dos veces ("Registré dos gastos de
  35.000 COP") y hasta tres cuando dos mensajes llegaban casi juntos.
- El guardrail de tema creía que el último turno era del usuario y no de
  Tresqu, así que un "sí" o un "🇨🇴 COP" dejaba de leerse como continuación y
  terminaba en el aviso de "solo puedo ayudarte con tus finanzas".

``without_current_message`` quita esa copia. ``user_turn`` pone en fila los
turnos de un mismo usuario: si escribe "12000 gaseosa" y a los cinco segundos
"12000 préstamo amigo", el segundo turno espera a que el primero termine y
guarde su respuesta, y así ve en el historial que la gaseosa ya quedó
registrada en vez de registrarla otra vez.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from asgiref.sync import sync_to_async
from django.core.cache import cache
from langchain_core.messages import HumanMessage

from telegrambot.config import AGENT_EXECUTION_TIMEOUT

logger = logging.getLogger(__name__)

# Hasta dónde se busca la copia del mensaje actual. Con turnos en paralelo la
# copia no siempre es el último mensaje: la respuesta al turno anterior puede
# haberse guardado después.
_CURRENT_LOOKBACK = 4


def _norm(text) -> str:
    return " ".join(str(text or "").split())


def without_current_message(history: list, raw_text: str) -> list:
    """Devuelve el historial sin la copia guardada del mensaje que se procesa.

    Busca, entre los últimos mensajes, el más reciente del usuario cuyo texto
    coincide con ``raw_text``. También cuenta como copia cuando ``raw_text``
    TERMINA con ese texto: WhatsApp antepone el mensaje citado ("[Respondiendo
    al mensaje anterior: …]") al texto que guardó.
    """

    current = _norm(raw_text)
    if not history or not current:
        return list(history or [])

    start = max(0, len(history) - _CURRENT_LOOKBACK)
    for index in range(len(history) - 1, start - 1, -1):
        message = history[index]
        if not isinstance(message, HumanMessage):
            continue
        stored = _norm(message.content)
        if stored and (stored == current or current.endswith(stored)):
            return history[:index] + history[index + 1:]
    return list(history)


# --- Turnos en fila ----------------------------------------------------------

# Lo que puede durar un turno completo: el supervisor tiene su propio timeout y
# después falta guardar la respuesta. Si el proceso muere con el candado
# puesto, caduca solo.
_LOCK_TTL = int(AGENT_EXECUTION_TIMEOUT) + 60
# Cuánto espera un turno a que termine el anterior. Pasado ese tiempo sigue de
# todas formas: un candado nunca deja a Tresqu sin responder.
_WAIT_SECONDS = int(AGENT_EXECUTION_TIMEOUT) + 30
_POLL_SECONDS = 0.5


def _lock_key(user_id) -> str:
    return f"agents:turn:{user_id}"


def _acquire(key: str, token: str) -> bool:
    try:
        return bool(cache.add(key, token, _LOCK_TTL))
    except Exception as exc:  # noqa: BLE001 — la caché nunca rompe el flujo
        logger.warning("turno en fila: no se pudo tomar el candado (%s); sigue sin él", exc)
        return True


def _release(key: str, token: str) -> None:
    try:
        if cache.get(key) == token:
            cache.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("turno en fila: no se pudo soltar el candado (%s)", exc)


_aacquire = sync_to_async(_acquire)
_arelease = sync_to_async(_release)


@asynccontextmanager
async def user_turn(user_id):
    """Ejecuta el bloque cuando ningún otro turno del usuario esté en curso.

    Envuelve el turno completo: cargar historial, correr el agente y guardar
    la respuesta. Si la respuesta se guardara fuera del bloque, el turno
    siguiente podría leer el historial justo antes y no verla.
    """

    key = _lock_key(user_id)
    token = uuid.uuid4().hex
    deadline = time.monotonic() + _WAIT_SECONDS
    acquired = await _aacquire(key, token)
    while not acquired and time.monotonic() < deadline:
        await asyncio.sleep(_POLL_SECONDS)
        acquired = await _aacquire(key, token)
    if not acquired:
        logger.warning(
            "turno en fila: el usuario %s sigue con un turno en curso tras %ss; se procesa igual",
            user_id, _WAIT_SECONDS,
        )
    try:
        yield
    finally:
        if acquired:
            await _arelease(key, token)
