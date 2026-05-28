"""Smoke tests for the web-chat streaming event extraction.

No network / LLM / DB: ``build_supervisor`` is replaced by a fake whose
``astream`` yields synthetic LangGraph ``updates`` chunks, and the category /
date / risk-session helpers are monkeypatched. This pins the mapping from graph
updates → ``step``/``final`` events that the SSE view streams to the frontend.

    python -m agents.tests.test_chat_stream_smoke
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

failures: list[str] = []


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _expect(cond: bool, label: str) -> None:
    print(f"    {'OK ' if cond else 'FAIL'} — {label}")
    if not cond:
        failures.append(label)


@dataclass
class _StubUser:
    id: int = 1


class _FakeSupervisor:
    """Yields synthetic ``stream_mode='updates'`` chunks like create_agent does."""

    def __init__(self, updates):
        self._updates = updates

    async def astream(self, _input, *, stream_mode=None, config=None):
        for update in self._updates:
            yield update


def _patch_common(chat_stream):
    async def _no_session(_uid):
        return False

    async def _cats(_user):
        return ("Gastos: café", "Ingresos: salario")

    chat_stream.risk_profiler_service.is_session_active = _no_session  # type: ignore[assignment]
    chat_stream._load_categories = _cats  # type: ignore[assignment]
    chat_stream._user_today = lambda _user: "2026-05-28"  # type: ignore[assignment]


def _collect(chat_stream, user, text):
    async def _run_async():
        return [ev async for ev in chat_stream.stream_agent_events(user, text, "web", [])]

    return asyncio.run(_run_async())


def _run() -> int:
    from langchain_core.messages import AIMessage, ToolMessage

    from agents import chat_stream

    _patch_common(chat_stream)
    user = _StubUser()

    print("\n[wallbit read] delegate → result → final, no pending")
    updates = [
        {"model": {"messages": [AIMessage(
            content="",
            tool_calls=[{"name": "manage_wallbit", "args": {"instruction": "dame mi saldo"}, "id": "1", "type": "tool_call"}],
        )]}},
        {"tools": {"messages": [ToolMessage(content="Saldo: 100 USD", name="manage_wallbit", tool_call_id="1")]}},
        {"model": {"messages": [AIMessage(content="Tienes 100 USD disponibles.")]}},
    ]
    chat_stream.build_supervisor = lambda **kw: (_FakeSupervisor(updates), {"confirmation": None}, {"first_step": None})  # type: ignore[assignment]
    events = _collect(chat_stream, user, "cuánto tengo en wallbit")

    steps = [e for e in events if e["type"] == "step"]
    finals = [e for e in events if e["type"] == "final"]
    _expect(len(finals) == 1, "exactly one final event")
    _expect(steps and steps[0]["phase"] == "delegate" and steps[0]["agent"] == "wallbit", "first step = delegate to wallbit")
    _expect(steps[0]["instruction"] == "dame mi saldo", "delegate carries the instruction")
    _expect(steps[0]["label"] == "Wallbit", "agent label resolved")
    _expect(any(e["phase"] == "result" and e["agent"] == "wallbit" for e in steps), "result step for wallbit")
    _expect(finals[0]["text"] == "Tienes 100 USD disponibles.", "final text = last AIMessage content")
    _expect(finals[0]["pending_confirmation"] is None, "no pending confirmation")

    print("\n[wallbit buy] pending_confirmation surfaced in final")
    pending = {"confirmation": None}
    preview = {"requires_confirmation": True, "confirmation_id": 42, "preview": {"summary": "BUY AAPL 20 USD"}}

    buy_updates = [
        {"model": {"messages": [AIMessage(
            content="",
            tool_calls=[{"name": "manage_wallbit", "args": {"instruction": "compra 20 USD de AAPL"}, "id": "2", "type": "tool_call"}],
        )]}},
        # Mimic the wallbit tool wrapper mutating pending_container mid-stream.
        {"tools": {"messages": [ToolMessage(content="preview generado", name="manage_wallbit", tool_call_id="2")]}},
        {"model": {"messages": [AIMessage(content="Confirma la compra.")]}},
    ]

    class _MutatingSupervisor(_FakeSupervisor):
        async def astream(self, _input, *, stream_mode=None, config=None):
            for i, update in enumerate(self._updates):
                if i == 1:
                    pending["confirmation"] = preview
                yield update

    chat_stream.build_supervisor = lambda **kw: (_MutatingSupervisor(buy_updates), pending, {"first_step": None})  # type: ignore[assignment]
    buy_events = _collect(chat_stream, user, "compra 20 de apple")
    buy_final = next(e for e in buy_events if e["type"] == "final")
    _expect(buy_final["pending_confirmation"] == preview, "final carries pending confirmation from container")

    print("\n[direct answer] greeting → only a final, no steps")
    greet_updates = [{"model": {"messages": [AIMessage(content="¡Hola! ¿En qué te ayudo?")]}}]
    chat_stream.build_supervisor = lambda **kw: (_FakeSupervisor(greet_updates), {"confirmation": None}, {"first_step": None})  # type: ignore[assignment]
    greet_events = _collect(chat_stream, user, "hola")
    _expect(not [e for e in greet_events if e["type"] == "step"], "no step events for a direct answer")
    _expect(greet_events[-1]["text"] == "¡Hola! ¿En qué te ayudo?", "final greeting text")

    if failures:
        print(f"\nCHAT STREAM SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nCHAT STREAM SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
