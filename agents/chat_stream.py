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

from langchain_core.messages import HumanMessage

from telegrambot.config import (
    AGENT_EXECUTION_TIMEOUT,
    AGENT_MAX_ITERATIONS,
    ERROR_MESSAGES,
)
from users.models import User

from . import risk_profiler_service
from .services import (
    RISK_PROFILE_COMMAND,
    _load_categories,
    _resume_or_start_profiler,
    _route_with_active_session,
    _user_today,
)
from .supervisor import build_supervisor

logger = logging.getLogger(__name__)

_SUMMARY_MAX_CHARS = 600

# Supervisor tool name → stable node id + human label for the agent graph.
_AGENT_LABELS: dict[str, dict[str, str]] = {
    "manage_expenses_and_income": {"id": "expenses", "label": "Gastos e ingresos"},
    "manage_wallbit": {"id": "wallbit", "label": "Wallbit"},
    "analyze_investment": {"id": "analyst", "label": "Analista"},
    "start_risk_profiler": {"id": "risk", "label": "Perfil de riesgo"},
}


def _agent_meta(tool_name: str) -> dict[str, str]:
    return _AGENT_LABELS.get(
        tool_name, {"id": tool_name or "unknown", "label": tool_name or "Agente"}
    )


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
