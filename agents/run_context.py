"""Rastreo por ejecución de las transacciones creadas por las tools del agente.

Los handlers de WhatsApp/Telegram necesitan saber qué filas de Expense/Income
se crearon durante UN run del agente para vincularlas al mensaje de
confirmación saliente (eliminación determinista vía swipe-to-reply y, a
futuro, reacciones). Las tools se ejecutan en lo profundo del stack de
LangChain, así que la propagación se hace con un ContextVar: el handler
activa el rastreo antes de invocar al agente y lee la lista al terminar.

LangChain (run_in_executor con copy_context), asgiref (sync_to_async) y
asyncio.to_thread copian el contexto hacia los hilos de trabajo; como el
valor rastreado es una LISTA mutable compartida, los appends hechos en esas
copias son visibles para el contexto del handler.

Si el rastreo no está activo (p. ej. runs del bot de Telegram o del chat
web que aún no lo usan), ``record_created_transaction`` es un no-op.
"""

from __future__ import annotations

from contextvars import ContextVar

_created_transactions: ContextVar[list | None] = ContextVar(
    "created_transactions", default=None
)


def start_transaction_tracking() -> list:
    """Activa el rastreo para el contexto actual y devuelve el contenedor.

    El caller debe conservar la referencia devuelta: es la misma lista que
    las tools van llenando, sin importar en qué hilo/copia de contexto corran.
    """
    container: list = []
    _created_transactions.set(container)
    return container


def record_created_transaction(kind: str, transaction_id) -> None:
    """Registra una transacción creada por una tool (``kind``: 'expense'|'income')."""
    container = _created_transactions.get()
    if container is not None:
        container.append({"kind": kind, "id": transaction_id})
