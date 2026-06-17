"""Celery tasks for the WhatsApp (Meta) bot.

El webhook de Meta encola aquí cada mensaje y responde 200 de inmediato. Mover
el procesamiento fuera del request HTTP elimina la causa raíz del bug en que un
deploy/reinicio del proceso web mataba el procesamiento a mitad de camino y
dejaba el candado anti-duplicados huérfano, haciendo que los reintentos de Meta
se descartaran como "duplicados" y el usuario nunca recibiera respuesta.

Garantías:

* ``acks_late`` + ``reject_on_worker_lost``: si el worker muere mientras
  procesa (p. ej. durante un deploy), el broker re-entrega la tarea y otro
  worker la termina. La durabilidad la da Redis, no la caché.
* Idempotencia anclada al *resultado*: el marcador ``done`` solo se escribe al
  terminar con éxito, así que colapsa reentregas reales del webhook sin tragarse
  mensajes que quedaron a medias.
* El candado de concurrencia es ``task_id``-aware: un reintento de la *misma*
  tarea (tras pérdida del worker) puede retomar su propio candado en vez de
  auto-descartarse.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Mismo prefijo histórico para el candado (evita dejar claves viejas colgando).
MESSAGE_LOCK_PREFIX = "whatsapp_message_processing_"
MESSAGE_DONE_PREFIX = "whatsapp_message_done_"

# El candado debe cubrir el peor caso de procesamiento (hard time limit = 5 min)
# para que dos entregas distintas no procesen en paralelo. Un reintento de la
# misma tarea no se bloquea con él (ver lógica task_id-aware más abajo).
MESSAGE_LOCK_TIMEOUT = 6 * 60  # 6 minutos
# Tras procesar con éxito, recordamos el mensaje un buen rato para colapsar
# cualquier reentrega tardía del webhook de Meta.
MESSAGE_DONE_TIMEOUT = 24 * 60 * 60  # 24 horas


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def process_meta_message_async(self, message, contacts_dict, waba_id):
    """Procesa un mensaje entrante de Meta de forma idempotente y resiliente."""
    # Import diferido para evitar el ciclo views <-> tasks.
    from .views import _process_single_meta_message

    message_id = message.get("id")
    if not message_id:
        logger.warning("Mensaje Meta sin ID; se omite")
        return

    done_key = f"{MESSAGE_DONE_PREFIX}{message_id}"
    lock_key = f"{MESSAGE_LOCK_PREFIX}{message_id}"
    task_id = self.request.id

    # 1. Idempotencia: si ya se procesó con éxito, no repetir. Esto colapsa las
    #    reentregas del mismo webhook por parte de Meta.
    if cache.get(done_key):
        logger.info(
            f"⏭️ Mensaje Meta ya procesado, se ignora - ID: {message_id}")
        return

    # 2. Candado de concurrencia. cache.add es atómico: solo una ejecución lo
    #    obtiene. Si el candado ya existe pero pertenece a ESTA misma tarea
    #    (reintento tras pérdida del worker), lo retomamos; si es de otra tarea,
    #    salimos para no procesar en paralelo.
    acquired = cache.add(lock_key, task_id, MESSAGE_LOCK_TIMEOUT)
    if not acquired and cache.get(lock_key) != task_id:
        logger.info(
            f"⏳ Mensaje Meta en proceso por otra tarea, se ignora - ID: {message_id}")
        return

    try:
        _process_single_meta_message(message, contacts_dict, waba_id)
        # Éxito: marcamos como procesado para colapsar reentregas futuras.
        cache.set(done_key, True, MESSAGE_DONE_TIMEOUT)
        logger.info(f"✅ Mensaje Meta completado - ID: {message_id}")
    except Exception:
        # Error transitorio: liberamos el candado para permitir el reintento
        # (autoretry_for) y re-lanzamos para que Celery lo gestione.
        logger.exception(
            f"Error procesando mensaje Meta, se reintentará - ID: {message_id}")
        cache.delete(lock_key)
        raise
    else:
        # Tras éxito el marcador 'done' ya impide el reproceso; soltamos el
        # candado para no retener la clave hasta su expiración.
        cache.delete(lock_key)
