"""Memoria semántica sobre el historial de conversación.

Los mensajes (``users.models.Message``) se guardan con embedding desde
``create_message``; este módulo es quien los consulta:

- ``get_semantic_context`` complementa la ventana reciente de
  ``fetch_last_messages`` con mensajes antiguos relevantes al texto entrante.
  Se inyecta en el prompt del supervisor en cada turno.
- ``search_history`` es la búsqueda explícita que el supervisor expone como
  tool (``search_conversation_history``).

Ambas son síncronas (ORM + llamada de embeddings); los callers async deben
envolverlas con ``sync_to_async``.
"""

from __future__ import annotations

import logging

from users.models import Message, User

logger = logging.getLogger(__name__)

# Cuántos mensajes recientes excluir: ya van completos en el historial que
# recibe el supervisor (ventana de fetch_last_messages), repetirlos solo mete ruido.
RECENT_WINDOW = 10

# Umbral heurístico de distancia coseno para text-embedding-3-small: por
# debajo el mensaje suele compartir tema con la consulta; por encima es ruido.
MAX_DISTANCE = 0.60

CONTEXT_LIMIT = 5
SEARCH_LIMIT = 8

# Los mensajes largos (resúmenes mensuales, listados) se truncan para no
# inflar el prompt: lo relevante para la memoria casi siempre está al inicio.
SNIPPET_LENGTH = 300


def _embed(text: str):
    from telegrambot.tools import embeddings

    return embeddings.embed_query(text)


def _recent_message_ids(user: User, window: int = RECENT_WINDOW) -> list[int]:
    return list(
        Message.objects.filter(chat__user=user)
        .order_by('-created_at')[:window]
        .values_list('id', flat=True)
    )


def _format_message(msg: Message) -> str:
    role = "Usuario" if msg.message_type == "incoming" else "Tresqu"
    text = " ".join(msg.text.split())
    if len(text) > SNIPPET_LENGTH:
        text = text[:SNIPPET_LENGTH] + "…"
    return f"[{msg.created_at:%Y-%m-%d}] {role}: {text}"


def get_semantic_context(user: User, query_text: str) -> str | None:
    """
    Devuelve un bloque de texto con mensajes pasados relevantes al mensaje
    entrante (fuera de la ventana reciente), o ``None`` si no hay nada
    suficientemente similar. Nunca lanza: ante cualquier error devuelve None
    para que el flujo del agente siga sin memoria semántica.
    """
    if not query_text or not query_text.strip():
        return None
    try:
        vector = _embed(query_text)
        recent_ids = _recent_message_ids(user)
        similar = Message.find_similar(
            user, vector, limit=CONTEXT_LIMIT, exclude_ids=recent_ids
        )
        relevant = [m for m in similar if m.distance <= MAX_DISTANCE]
        if not relevant:
            return None
        # find_similar ordena por similitud; para el prompt es más útil el
        # orden cronológico.
        relevant.sort(key=lambda m: m.created_at)
        return "\n".join(_format_message(m) for m in relevant)
    except Exception as exc:
        logger.warning(
            f"Memoria semántica no disponible para usuario {user.id}: {exc}"
        )
        return None


def search_history(user: User, query: str, limit: int = SEARCH_LIMIT) -> list[str]:
    """
    Búsqueda semántica explícita en TODO el historial (sin excluir la ventana
    reciente: el usuario puede preguntar por algo que acaba de decir).
    Devuelve mensajes formateados con fecha y rol, del más antiguo al más
    reciente. Lista vacía si no hay nada relevante.
    """
    if not query or not query.strip():
        return []
    vector = _embed(query)
    similar = Message.find_similar(user, vector, limit=limit)
    relevant = [m for m in similar if m.distance <= MAX_DISTANCE]
    relevant.sort(key=lambda m: m.created_at)
    return [_format_message(m) for m in relevant]
