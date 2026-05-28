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

from . import risk_profiler_service
from .agent_directory import SPECIALIST_IDS, SUPERVISOR_ID, agent_meta_by_tool
from .effective_profile import get_effective_profile
from .services import (
    RISK_PROFILE_COMMAND,
    _load_categories,
    _resume_or_start_profiler,
    _route_with_active_session,
    _user_today,
)
from .subagents.analyst import build_analyst_subagent
from .subagents.expenses import build_expenses_subagent
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


async def stream_agent_events(
    user: User,
    raw_text: str,
    channel: str,
    history: list,
) -> AsyncIterator[dict[str, Any]]:
    """Yield the agent-interaction events for a single web-chat turn."""

    try:
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

    expense_categories_str, income_categories_str = await _load_categories(user)
    current_date = _user_today(user)

    supervisor, pending_container, risk_profiler_signal = build_supervisor(
        user=user,
        channel=channel,
        user_message=raw_text,
        expense_categories_str=expense_categories_str,
        income_categories_str=income_categories_str,
        current_date=current_date,
    )

    messages = list(history) + [HumanMessage(content=raw_text)]
    final_text = ""

    try:
        async with asyncio.timeout(float(AGENT_EXECUTION_TIMEOUT)):
            async for update in supervisor.astream(
                {"messages": messages},
                stream_mode="updates",
                config={"recursion_limit": AGENT_MAX_ITERATIONS},
            ):
                for node_update in update.values():
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
            async for event in stream_agent_events(user, raw_text, channel, history):
                yield event
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


_TOLERANCE_ES = {
    "conservative": "conservador",
    "moderate": "moderado",
    "aggressive": "agresivo",
}


def _format_risk_readout(eff) -> str:
    """Plain-language description of the user's current effective risk profile."""

    label = _TOLERANCE_ES.get(eff.tolerance, eff.tolerance)

    if eff.source == "default":
        return (
            "Aún no tienes un perfil de riesgo definido. Toca *Iniciar evaluación* "
            "para armarlo con el cuestionario, o registra más movimientos para que "
            "se infiera automáticamente."
        )

    if eff.source == "inferred":
        return (
            f"De momento tu perfil es *{label}*, inferido automáticamente de tus "
            "registros (todavía sin cuestionario). Para mayor precisión, toca "
            "*Iniciar evaluación* y responde las 5 preguntas."
        )

    fuente = {
        "declared": "según tu evaluación",
        "agreement": "confirmado por tu evaluación y tus registros",
        "safety_cap": "ajustado a un perfil más prudente por tu situación actual",
    }.get(eff.source, "según tu evaluación")

    text = f"Sí, ya tienes un perfil: *{label}* ({fuente})."
    if eff.warning:
        text += f"\n\n_{eff.warning}_"
    text += "\n\n¿Quieres reevaluarlo? Toca *Iniciar evaluación*."
    return text


async def _stream_risk(
    user: User,
    channel: str,
    raw_text: str,
    history: list,
) -> AsyncIterator[dict[str, Any]]:
    """Handle the risk-profile agent directly (never falls back to the supervisor).

    Starts the guided Q&A with ``RISK_PROFILE_COMMAND``, continues an active
    session, and otherwise answers about the user's current profile from data.
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

    try:
        eff = await sync_to_async(get_effective_profile)(user, refresh_inference=False)
        text = _format_risk_readout(eff)
    except Exception as exc:  # noqa: BLE001
        logger.exception("risk profile readout failed for user=%s: %s", user.id, exc)
        text = (
            "No pude leer tu perfil ahora mismo. Puedes evaluarlo tocando "
            "*Iniciar evaluación*."
        )
    yield {"type": "final", "text": text, "pending_confirmation": None}


async def _stream_specialist(
    user: User,
    agent_id: str,
    channel: str,
    raw_text: str,
    history: list,
) -> AsyncIterator[dict[str, Any]]:
    """Run a single specialist subagent directly and stream its tool calls."""

    try:
        if agent_id == "expenses":
            expense_categories_str, income_categories_str = await _load_categories(user)
            agent = build_expenses_subagent(
                user, expense_categories_str, income_categories_str, _user_today(user)
            )
        elif agent_id == "wallbit":
            agent = build_wallbit_subagent(user, channel, raw_text)
        elif agent_id == "analyst":
            agent = build_analyst_subagent(user)
        else:
            yield {"type": "error", "text": "Agente desconocido."}
            return

        messages = list(history) + [HumanMessage(content=raw_text)]
        final_text = ""
        collected: list = []

        async with asyncio.timeout(float(AGENT_EXECUTION_TIMEOUT)):
            async for update in agent.astream(
                {"messages": messages},
                stream_mode="updates",
                config={"recursion_limit": 20},
            ):
                for node_update in update.values():
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
        logger.error("specialist %s stream timeout", agent_id)
        yield {"type": "error", "text": ERROR_MESSAGES["timeout"]}
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("specialist %s stream failed: %s", agent_id, exc)
        yield {"type": "error", "text": ERROR_MESSAGES["default"]}
        return

    # Only Wallbit can produce a real-money confirmation preview.
    pending = extract_pending_confirmation(collected) if agent_id == "wallbit" else None
    yield {"type": "final", "text": final_text, "pending_confirmation": pending}
