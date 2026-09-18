"""Streaming entry point for the web chat.

Unlike ``services.process_message`` (which runs the supervisor with a single
blocking ``ainvoke`` and returns only the final text), this module streams the
supervisor's *interaction trace* so the web UI can show, live, which subagent
the supervisor delegated to and what it returned.

It reuses the same routing as ``process_message`` (``/perfil``, an active
risk-profiler session, and the multi-agent supervisor). The risk-profiler
branches are not multi-agent turns, so they emit a single ``final`` event; only
the supervisor path streams ``step`` events.

Event shapes yielded by ``stream_agent_events``:
    {"type": "step", "phase": "delegate", "agent": <id>, "label": <str>, "instruction": <str>}
    {"type": "step", "phase": "result",   "agent": <id>, "label": <str>, "summary": <str>}
    {"type": "final", "text": <str>, "pending_confirmation": <dict|None>}
    {"type": "error", "text": <str>}
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage

from telegrambot.config import (
    AGENT_EXECUTION_TIMEOUT,
    AGENT_MAX_ITERATIONS,
    ERROR_MESSAGES,
)
from users.models import User
from whatsappbot.wallbit_handlers import extract_pending_confirmation

from . import relevance_guard, risk_profiler_service
from .agent_directory import SPECIALIST_IDS, SUPERVISOR_ID, agent_meta_by_tool
from .conversation_memory import get_semantic_context
from .services import (
    RISK_PROFILE_COMMAND,
    _load_categories,
    _resume_or_start_profiler,
    _route_with_active_session,
    _user_today,
)
from .subagents.analyst import build_analyst_subagent
from .currency_guard import conversation_texts
from .subagents.expenses import build_expenses_subagent
from .subagents.risk import build_risk_subagent
from .subagents.wallbit import build_wallbit_subagent
from .supervisor import build_supervisor

logger = logging.getLogger(__name__)

_SUMMARY_MAX_CHARS = 600

# Friendly labels for the specialist subagents' own tools (shown in the direct
# 1:1 chat trace). Unknown tools fall back to a generic humanizer.
_TOOL_LABELS: dict[str, str] = {
    "wallbit_get_balance_for_user": "Consultar saldo",
    "wallbit_list_transactions_for_user": "Listar movimientos",
    "wallbit_search_assets_for_user": "Buscar activos",
    "wallbit_get_asset_for_user": "Ficha de activo",
    "tresqu_query_history": "Búsqueda en tu historial",
    "wallbit_place_trade": "Proponer operación",
    "wallbit_move_funds": "Mover fondos",
    "wallbit_deposit_chest": "Depósito Robo Advisor",
    "wallbit_withdraw_chest": "Retiro Robo Advisor",
    "wallbit_set_card_status": "Estado de tarjeta",
    "get_asset_quote": "Cotización del activo",
    "get_price_history": "Histórico de precios",
    "get_user_risk_profile": "Leer perfil de riesgo",
    "get_user_portfolio": "Leer portafolio",
    "get_risk_profile_status": "Leer tu perfil",
    "recompute_inference": "Recalcular inferencia",
}


def _guard_final(verdict: "relevance_guard.GuardVerdict") -> dict[str, Any]:
    """Evento ``final`` que cierra un turno cortado por el guardrail.

    ``silent`` le dice al frontend que no pinte burbuja: el turno no existe.
    """
    return {
        "type": "final",
        "text": verdict.reply or "",
        "pending_confirmation": None,
        "silent": verdict.silent,
    }


def _humanize_tool(name: str) -> str:
    if name in _TOOL_LABELS:
        return _TOOL_LABELS[name]
    cleaned = (
        (name or "")
        .replace("_for_user", "")
        .replace("wallbit_", "")
        .replace("tresqu_", "")
        .replace("_", " ")
        .strip()
    )
    return cleaned.capitalize() or "Herramienta"


def _tool_args_summary(args: Any) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    parts = [f"{k}: {v}" for k, v in args.items() if v not in (None, "")]
    return ", ".join(parts)[:200]


def _agent_meta(tool_name: str) -> dict[str, str]:
    return agent_meta_by_tool(tool_name)


def _as_text(content: Any) -> str:
    """Coerce a message content (str or list of parts) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        ]
        return "".join(parts)
    return str(content or "")


def _answer_token(chunk: Any) -> str | None:
    """Extract the answer-token text from a ``messages``-mode chunk, or None.

    We token-stream only the *user-facing* answer: chunks emitted by the
    outermost graph's ``model`` node that carry content and aren't a tool call.
    The delegation (tool-call) chunk has ``tool_calls`` set, tool results live on
    the ``tools`` node, and nested subagents run via ``ainvoke`` so their tokens
    never reach this stream — but we still require depth-0 (no ``|`` in the
    LangGraph checkpoint namespace) so a future nested model can't leak in.
    """
    try:
        msg, meta = chunk
    except (TypeError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("langgraph_node") != "model":
        return None
    if "|" in (meta.get("langgraph_checkpoint_ns") or ""):
        return None
    if getattr(msg, "tool_calls", None):
        return None
    return _as_text(getattr(msg, "content", "")) or None


async def stream_agent_events(
    user: User,
    raw_text: str,
    channel: str,
    history: list,
) -> AsyncIterator[dict[str, Any]]:
    """Yield the agent-interaction events for a single web-chat turn."""

    try:
        blocked = await relevance_guard.check_flood_async(
            relevance_guard.scope_key(channel, user.id), raw_text
        )
        if blocked:
            yield _guard_final(blocked)
            return

        normalized = raw_text.strip().lower()

        if normalized == RISK_PROFILE_COMMAND:
            resp = await _resume_or_start_profiler(
                user, channel, start_fresh=True, user_message=raw_text, history=history
            )
            yield {"type": "final", "text": resp.text, "pending_confirmation": resp.pending_confirmation}
            return

        if await risk_profiler_service.is_session_active(user.id):
            resp = await _route_with_active_session(user, channel, raw_text, history)
            yield {"type": "final", "text": resp.text, "pending_confirmation": resp.pending_confirmation}
            return

        async for event in _stream_supervisor(user, channel, raw_text, history):
            yield event

    except Exception as exc:  # noqa: BLE001 — surface a clean message, never crash the stream
        logger.exception("stream_agent_events unexpected error: %s", exc)
        yield {"type": "error", "text": ERROR_MESSAGES["default"]}


async def _stream_supervisor(
    user: User,
    channel: str,
    raw_text: str,
    history: list,
) -> AsyncIterator[dict[str, Any]]:
    """Run the supervisor with ``astream`` and emit step + final events."""

    verdict = await relevance_guard.check_relevance(
        relevance_guard.scope_key(channel, user.id), raw_text, history
    )
    if not verdict.allow:
        yield _guard_final(verdict)
        return

    expense_categories_str, income_categories_str = await _load_categories(user)
    current_date = _user_today(user)

    # Memoria semántica sobre los mensajes persistidos (WhatsApp/Telegram):
    # el chat web es efímero, pero así puede recordar lo hablado en otros canales.
    semantic_context = await sync_to_async(get_semantic_context)(user, raw_text)

    supervisor, pending_container, risk_profiler_signal = build_supervisor(
        user=user,
        channel=channel,
        user_message=raw_text,
        expense_categories_str=expense_categories_str,
        income_categories_str=income_categories_str,
        current_date=current_date,
        semantic_context=semantic_context,
        history=history,
    )

    messages = list(history) + [HumanMessage(content=raw_text)]
    final_text = ""

    try:
        async with asyncio.timeout(float(AGENT_EXECUTION_TIMEOUT)):
            async for mode, data in supervisor.astream(
                {"messages": messages},
                stream_mode=["updates", "messages"],
                config={"recursion_limit": AGENT_MAX_ITERATIONS},
            ):
                if mode == "messages":
                    token = _answer_token(data)
                    if token:
                        yield {"type": "token", "text": token}
                    continue
                # mode == "updates": one entry per graph node that just ran.
                for node_update in data.values():
                    if not isinstance(node_update, dict):
                        continue
                    for msg in node_update.get("messages", []) or []:
                        kind = msg.__class__.__name__
                        if kind == "AIMessage":
                            tool_calls = getattr(msg, "tool_calls", None) or []
                            if tool_calls:
                                for tc in tool_calls:
                                    meta = _agent_meta(tc.get("name", ""))
                                    instruction = (tc.get("args") or {}).get("instruction", "")
                                    yield {
                                        "type": "step",
                                        "phase": "delegate",
                                        "agent": meta["id"],
                                        "label": meta["label"],
                                        "instruction": instruction,
                                    }
                            else:
                                text = _as_text(getattr(msg, "content", ""))
                                if text.strip():
                                    final_text = text
                        elif kind == "ToolMessage":
                            meta = _agent_meta(getattr(msg, "name", "") or "")
                            summary = _as_text(getattr(msg, "content", ""))[:_SUMMARY_MAX_CHARS]
                            yield {
                                "type": "step",
                                "phase": "result",
                                "agent": meta["id"],
                                "label": meta["label"],
                                "summary": summary,
                            }
    except (asyncio.TimeoutError, TimeoutError):
        logger.error("Supervisor stream timeout")
        yield {"type": "error", "text": ERROR_MESSAGES["timeout"]}
        return
    except Exception as exc:  # noqa: BLE001
        error_str = str(exc).lower()
        if "timeout" in error_str:
            yield {"type": "error", "text": ERROR_MESSAGES["timeout"]}
        elif any(p in error_str for p in ("ssl", "eof detected")):
            yield {"type": "error", "text": ERROR_MESSAGES["ssl"]}
        elif any(p in error_str for p in ("connection", "network")):
            yield {"type": "error", "text": ERROR_MESSAGES["connection"]}
        else:
            logger.exception("Supervisor stream failed: %s", exc)
            yield {"type": "error", "text": ERROR_MESSAGES["default"]}
        return

    # If the supervisor started the risk profiler, surface the raw question
    # instead of its wrapped recap (mirrors services._run_supervisor).
    first_step = risk_profiler_signal.get("first_step")
    if first_step and not first_step.get("done"):
        final_text = first_step.get("question") or final_text

    pending = pending_container.get("confirmation")
    yield {"type": "final", "text": final_text, "pending_confirmation": pending}


async def stream_single_agent(
    user: User,
    agent_id: str,
    raw_text: str,
    channel: str,
    history: list,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a turn addressed to one specific agent in the roster.

    - ``tresqu`` and ``risk`` share the supervisor-path routing: Tresqu
      orchestrates, and the risk profiler is started with ``RISK_PROFILE_COMMAND``
      and continued via the active-session logic in ``stream_agent_events``.
    - A specialist runs in isolation and streams its OWN tool calls so the user
      sees that single agent work (no supervisor, no other agents).
    """

    try:
        if agent_id == SUPERVISOR_ID:
            # stream_agent_events ya aplica el guardrail completo.
            async for event in stream_agent_events(user, raw_text, channel, history):
                yield event
            return

        blocked = await relevance_guard.check_flood_async(
            relevance_guard.scope_key(channel, user.id), raw_text
        )
        if blocked:
            yield _guard_final(blocked)
            return

        if agent_id == "risk":
            async for event in _stream_risk(user, channel, raw_text, history):
                yield event
            return

        if agent_id in SPECIALIST_IDS:
            async for event in _stream_specialist(
                user, agent_id, channel, raw_text, history
            ):
                yield event
            return

        yield {"type": "error", "text": "Agente desconocido."}
    except Exception as exc:  # noqa: BLE001 — never crash the stream
        logger.exception("stream_single_agent error (%s): %s", agent_id, exc)
        yield {"type": "error", "text": ERROR_MESSAGES["default"]}


async def _stream_risk(
    user: User,
    channel: str,
    raw_text: str,
    history: list,
) -> AsyncIterator[dict[str, Any]]:
    """Handle the risk-profile agent directly (never falls back to the supervisor).

    - ``RISK_PROFILE_COMMAND`` (the "Iniciar/Actualizar evaluación" button) starts
      the guided Q&A; an active session continues it.
    - Any other message runs the read-only conversational risk subagent, which
      reads the declared + inferred profile and explains/guides — it never sets
      the profile (that's only the audited Q&A).
    """

    normalized = raw_text.strip().lower()

    if normalized == RISK_PROFILE_COMMAND:
        resp = await _resume_or_start_profiler(
            user, channel, start_fresh=True, user_message=raw_text, history=history
        )
        yield {"type": "final", "text": resp.text, "pending_confirmation": None}
        return

    if await risk_profiler_service.is_session_active(user.id):
        resp = await _route_with_active_session(user, channel, raw_text, history)
        yield {
            "type": "final",
            "text": resp.text,
            "pending_confirmation": resp.pending_confirmation,
        }
        return

    verdict = await relevance_guard.check_relevance(
        relevance_guard.scope_key(channel, user.id), raw_text, history
    )
    if not verdict.allow:
        yield _guard_final(verdict)
        return

    agent = build_risk_subagent(user)
    async for event in _stream_agent_run(agent, "risk", raw_text, history):
        yield event


async def _stream_specialist(
    user: User,
    agent_id: str,
    channel: str,
    raw_text: str,
    history: list,
) -> AsyncIterator[dict[str, Any]]:
    """Run a single specialist subagent directly and stream its tool calls."""

    verdict = await relevance_guard.check_relevance(
        relevance_guard.scope_key(channel, user.id), raw_text, history
    )
    if not verdict.allow:
        yield _guard_final(verdict)
        return

    if agent_id == "expenses":
        expense_categories_str, income_categories_str = await _load_categories(user)
        agent = build_expenses_subagent(
            user,
            expense_categories_str,
            income_categories_str,
            _user_today(user),
            conversation_texts(raw_text, history),
        )
    elif agent_id == "wallbit":
        agent = build_wallbit_subagent(user, channel, raw_text)
    elif agent_id == "analyst":
        agent = build_analyst_subagent(user)
    else:
        yield {"type": "error", "text": "Agente desconocido."}
        return

    # Only Wallbit can produce a real-money confirmation preview.
    async for event in _stream_agent_run(
        agent, agent_id, raw_text, history, capture_pending=(agent_id == "wallbit")
    ):
        yield event


async def _stream_agent_run(
    agent,
    agent_id: str,
    raw_text: str,
    history: list,
    *,
    capture_pending: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Run a single agent with ``astream`` and emit its tool steps + final.

    Shared by the specialist and risk conversational chats: each tool call the
    agent makes becomes a step so the user sees that one agent work.
    """

    messages = list(history) + [HumanMessage(content=raw_text)]
    final_text = ""
    collected: list = []

    try:
        async with asyncio.timeout(float(AGENT_EXECUTION_TIMEOUT)):
            async for mode, data in agent.astream(
                {"messages": messages},
                stream_mode=["updates", "messages"],
                config={"recursion_limit": 20},
            ):
                if mode == "messages":
                    token = _answer_token(data)
                    if token:
                        yield {"type": "token", "text": token}
                    continue
                # mode == "updates": one entry per graph node that just ran.
                for node_update in data.values():
                    if not isinstance(node_update, dict):
                        continue
                    for msg in node_update.get("messages", []) or []:
                        collected.append(msg)
                        kind = msg.__class__.__name__
                        if kind == "AIMessage":
                            tool_calls = getattr(msg, "tool_calls", None) or []
                            if tool_calls:
                                for tc in tool_calls:
                                    yield {
                                        "type": "step",
                                        "phase": "delegate",
                                        "agent": agent_id,
                                        "label": _humanize_tool(tc.get("name", "")),
                                        "instruction": _tool_args_summary(tc.get("args")),
                                    }
                            else:
                                text = _as_text(getattr(msg, "content", ""))
                                if text.strip():
                                    final_text = text
                        elif kind == "ToolMessage":
                            yield {
                                "type": "step",
                                "phase": "result",
                                "agent": agent_id,
                                "label": _humanize_tool(getattr(msg, "name", "") or ""),
                                "summary": _as_text(getattr(msg, "content", ""))[
                                    :_SUMMARY_MAX_CHARS
                                ],
                            }
    except (asyncio.TimeoutError, TimeoutError):
        logger.error("%s stream timeout", agent_id)
        yield {"type": "error", "text": ERROR_MESSAGES["timeout"]}
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s stream failed: %s", agent_id, exc)
        yield {"type": "error", "text": ERROR_MESSAGES["default"]}
        return

    pending = extract_pending_confirmation(collected) if capture_pending else None
    yield {"type": "final", "text": final_text, "pending_confirmation": pending}
