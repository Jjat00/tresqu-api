"""Service layer for the Risk Profiler graph.

Owns the lifecycle of multi-turn Q&A sessions:

- ``is_session_active`` — does the user currently have a paused Q&A?
- ``start_session`` — open a fresh thread, run until first interrupt.
- ``resume_session`` — feed the user's answer back into a paused graph.
- ``abandon_session`` — clear a stale thread.

The graph itself lives in ``agents.graphs.risk_profiler``. This module
deliberately keeps the ``AsyncPostgresSaver`` open only for the lifetime
of each operation — state survives between turns because the saver writes
it to Postgres, not because we keep the saver around.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from django.conf import settings
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from telegrambot.config import OPENAI_MAX_RETRIES, OPENAI_REQUEST_TIMEOUT

from .graphs.risk_profiler import (
    QUESTIONS,
    compile_risk_profiler_graph,
    is_session_stale,
    thread_id_for,
)

# Result type for ``classify_message``. The LLM may also return "ambiguous" —
# we fold that into "qa_answer" by policy (prefer continuing the Q&A over
# accidentally bailing out).
ClassificationType = Literal["qa_answer", "other_intent", "cancel"]

logger = logging.getLogger(__name__)


# Cache so we only run the (idempotent) migration query once per process.
_setup_done = False


def _db_uri() -> str:
    """Return the DATABASE_URL the checkpointer should connect to.

    psycopg / langgraph-checkpoint-postgres requires the ``postgresql://``
    scheme (not ``postgres://``). Supabase frequently emits the short form.
    """

    uri = os.getenv("DATABASE_URL") or getattr(settings, "DATABASE_URL", "")
    if not uri:
        raise RuntimeError("DATABASE_URL is not set; risk profiler checkpointer cannot connect")
    if uri.startswith("postgres://"):
        uri = "postgresql://" + uri[len("postgres://"):]
    return uri


@asynccontextmanager
async def _open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """Open an ``AsyncPostgresSaver`` with server-side prepared statements OFF.

    ``AsyncPostgresSaver.from_conn_string`` hardcodes ``prepare_threshold=0``,
    which makes psycopg prepare every statement on the server. That breaks
    against Supabase's transaction-mode connection pooler (pgbouncer), which
    rotates physical connections and surfaces ``DuplicatePreparedStatement:
    prepared statement "_pg3_0" already exists``. Building the connection
    ourselves with ``prepare_threshold=None`` disables prepared statements so
    every checkpointer query works through the pooler. ``row_factory=dict_row``
    matches what the saver expects.
    """

    async with await AsyncConnection.connect(
        _db_uri(),
        autocommit=True,
        prepare_threshold=None,
        row_factory=dict_row,
    ) as conn:
        yield AsyncPostgresSaver(conn)


async def _ensure_setup(checkpointer: AsyncPostgresSaver) -> None:
    """Run checkpointer migrations once per process (idempotent on subsequent calls)."""

    global _setup_done
    if _setup_done:
        return
    try:
        await checkpointer.setup()
        _setup_done = True
    except Exception as exc:
        logger.exception(f"risk_profiler checkpointer setup failed: {exc}")
        raise


async def is_session_active(user_id: int) -> bool:
    """True if the user has a paused Q&A that hasn't been completed or expired."""

    config = {"configurable": {"thread_id": thread_id_for(user_id)}}
    try:
        async with _open_checkpointer() as cp:
            await _ensure_setup(cp)
            snapshot = await cp.aget_tuple(config)
    except Exception as exc:
        logger.exception(f"is_session_active failed for user={user_id}: {exc}")
        return False

    if not snapshot:
        return False

    checkpoint = snapshot.checkpoint
    values = (
        checkpoint.get("channel_values", {})
        if isinstance(checkpoint, dict)
        else getattr(checkpoint, "channel_values", {})
    ) or {}
    if values.get("status") == "completed":
        return False

    context = values.get("context") or {}
    if is_session_stale(context.get("loaded_at")):
        logger.info(f"risk_profiler session for user={user_id} is stale; treating as inactive")
        return False

    return True


async def start_session(user_id: int, channel: str) -> dict[str, Any]:
    """Start a fresh Q&A for the given user.

    Any pre-existing thread for this user is overwritten — the saver is
    keyed by ``thread_id`` so starting a new session creates new checkpoints
    in the same thread.

    Returns ``{"question": str, "step": int, "total": int}`` on the first
    interrupt, or ``{"final_text": str}`` if the graph somehow completes
    in a single pass (shouldn't happen with the current Q&A loop).
    """

    config = {"configurable": {"thread_id": thread_id_for(user_id)}}
    initial_state: dict[str, Any] = {
        "user_id": user_id,
        "channel": channel,
        "current_idx": 0,
        "answers": {},
    }

    async with _open_checkpointer() as cp:
        await _ensure_setup(cp)
        graph = compile_risk_profiler_graph(cp)
        # Clear any previous thread state for this user before starting fresh.
        try:
            await cp.adelete_thread(thread_id_for(user_id))
        except Exception as exc:  # pragma: no cover — best-effort cleanup
            logger.debug(f"adelete_thread for user={user_id} no-op or failed: {exc}")

        result = await graph.ainvoke(initial_state, config=config)

    return _extract_step_or_final(result)


async def resume_session(user_id: int, user_message: str) -> dict[str, Any]:
    """Feed the user's text answer into the paused graph and run to the next pause."""

    config = {"configurable": {"thread_id": thread_id_for(user_id)}}

    async with _open_checkpointer() as cp:
        await _ensure_setup(cp)
        graph = compile_risk_profiler_graph(cp)
        result = await graph.ainvoke(Command(resume=user_message), config=config)

    return _extract_step_or_final(result)


async def get_pending_question(user_id: int) -> str | None:
    """Return the text of the question the graph is currently paused on.

    Reads the checkpoint without advancing it. Returns ``None`` if there
    is no paused session or all questions have been answered.
    """

    config = {"configurable": {"thread_id": thread_id_for(user_id)}}
    try:
        async with _open_checkpointer() as cp:
            await _ensure_setup(cp)
            snapshot = await cp.aget_tuple(config)
    except Exception as exc:
        logger.exception(f"get_pending_question failed for user={user_id}: {exc}")
        return None

    if not snapshot:
        return None

    checkpoint = snapshot.checkpoint
    values = (
        checkpoint.get("channel_values", {})
        if isinstance(checkpoint, dict)
        else getattr(checkpoint, "channel_values", {})
    ) or {}
    idx = values.get("current_idx", 0)
    if idx >= len(QUESTIONS):
        return None
    return QUESTIONS[idx]["text"]


_CLASSIFIER_SYSTEM_TEMPLATE = """Clasificas mensajes del usuario durante un Q&A de perfil de inversión.

La pregunta pendiente actualmente es:
\"\"\"
{question}
\"\"\"

Categorías posibles:
- "qa_answer": el mensaje responde — directa o indirectamente — a la pregunta pendiente. Ejemplos: "5 años", "B", "moderado", "no estoy seguro, tal vez 50%", "vendería todo".
- "other_intent": el mensaje NO responde la pregunta sino que es otra cosa: registrar un gasto/ingreso, consultar saldo, pedir un resumen, comprar/vender activos, pregunta de naturaleza distinta. Ejemplos: "gasté 10k en café", "cuánto llevo este mes", "saldo wallbit", "compra 50 USD de AAPL", "cuál es el precio del bitcoin".
- "cancel": el usuario quiere salir del Q&A explícitamente. Ejemplos: "cancelar", "salir", "después", "ahora no", "olvidalo", "stop", "déjalo para luego".

Reglas:
- Si dudas entre qa_answer y other_intent, elige qa_answer (es menos costoso procesar mal una respuesta que romper el flujo).
- Solo elige other_intent si hay señales claras (ej. mención de un monto + verbo de transacción, comando explícito a Wallbit, etc.).
- Solo elige cancel si el usuario claramente quiere abandonar el Q&A.

Devuelve SOLO un JSON con este shape, sin explicación adicional:
{{"type": "qa_answer" | "other_intent" | "cancel", "confidence": <float 0.0-1.0>}}"""


async def classify_message(question: str, message: str) -> dict[str, Any]:
    """Decide whether ``message`` is an answer to ``question``, another intent, or a cancel.

    Uses a cheap fast model. Falls back to ``qa_answer`` on any failure.
    """

    llm = ChatOpenAI(
        model=getattr(settings, "AGENT_CLASSIFIER_MODEL", "gpt-4.1-nano"),
        temperature=0,
        api_key=settings.OPENAI_API_KEY,
        request_timeout=OPENAI_REQUEST_TIMEOUT,
        max_retries=OPENAI_MAX_RETRIES,
        model_kwargs={"response_format": {"type": "json_object"}},
    )

    try:
        response = await llm.ainvoke(
            [
                SystemMessage(content=_CLASSIFIER_SYSTEM_TEMPLATE.format(question=question)),
                HumanMessage(content=message),
            ]
        )
        parsed = json.loads(response.content)
        type_ = parsed.get("type", "qa_answer")
        if type_ not in ("qa_answer", "other_intent", "cancel"):
            type_ = "qa_answer"
        confidence = float(parsed.get("confidence", 0.7))
        return {"type": type_, "confidence": confidence}
    except Exception as exc:
        logger.exception(f"classify_message failed; defaulting to qa_answer: {exc}")
        return {"type": "qa_answer", "confidence": 0.5}


async def abandon_session(user_id: int) -> None:
    """Drop the user's thread state — used when /perfil is invoked again or stale."""

    async with _open_checkpointer() as cp:
        await _ensure_setup(cp)
        try:
            await cp.adelete_thread(thread_id_for(user_id))
        except Exception as exc:
            logger.exception(f"abandon_session failed for user={user_id}: {exc}")


def _extract_step_or_final(result: Any) -> dict[str, Any]:
    """Normalize ``graph.ainvoke`` result into a step or final payload.

    LangGraph 1.1 returns either:
    - dict with ``__interrupt__`` key (graph paused at interrupt())
    - dict with the final state (graph reached END)
    """

    # Modern API: result["__interrupt__"] is a tuple of Interrupt objects when paused.
    interrupts = None
    if isinstance(result, dict):
        interrupts = result.get("__interrupt__")
    else:
        interrupts = getattr(result, "interrupts", None)

    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        if isinstance(payload, dict):
            return {
                "question": payload.get("question", ""),
                "step": payload.get("step"),
                "total": payload.get("total") or len(QUESTIONS),
                "done": False,
            }
        return {"question": str(payload), "done": False, "total": len(QUESTIONS)}

    # Graph reached END — return the assembled final text from the persist node.
    final_text = None
    if isinstance(result, dict):
        final_text = result.get("final_text")
    else:
        final_text = getattr(result, "final_text", None)
    return {"final_text": final_text or "✅ Tu perfil quedó guardado.", "done": True}
