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


def _token_chunk(text, ns="model:1"):
    """Build a ``messages``-mode tuple like create_agent emits per answer token."""
    from langchain_core.messages import AIMessageChunk

    return (
        "messages",
        (
            AIMessageChunk(content=text),
            {"langgraph_node": "model", "langgraph_checkpoint_ns": ns},
        ),
    )


class _FakeSupervisor:
    """Yields synthetic ``stream_mode=['updates','messages']`` output like
    create_agent does: each update as ``('updates', update)``, plus any scripted
    answer tokens as ``('messages', (chunk, meta))``."""

    def __init__(self, updates, tokens=None):
        self._updates = updates
        self._tokens = tokens or []

    async def astream(self, _input, *, stream_mode=None, config=None):
        for update in self._updates:
            yield ("updates", update)
        for tok in self._tokens:
            yield _token_chunk(tok)


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


def _collect_single(chat_stream, user, agent_id, text):
    async def _run_async():
        return [
            ev
            async for ev in chat_stream.stream_single_agent(user, agent_id, text, "web", [])
        ]

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
    answer_tokens = ["Tienes ", "100 USD ", "disponibles."]
    chat_stream.build_supervisor = lambda **kw: (_FakeSupervisor(updates, tokens=answer_tokens), {"confirmation": None}, {"first_step": None})  # type: ignore[assignment]
    events = _collect(chat_stream, user, "cuánto tengo en wallbit")

    steps = [e for e in events if e["type"] == "step"]
    finals = [e for e in events if e["type"] == "final"]
    tokens = [e for e in events if e["type"] == "token"]
    _expect(len(finals) == 1, "exactly one final event")
    _expect(steps and steps[0]["phase"] == "delegate" and steps[0]["agent"] == "wallbit", "first step = delegate to wallbit")
    _expect(steps[0]["instruction"] == "dame mi saldo", "delegate carries the instruction")
    _expect(steps[0]["label"] == "Wallbit", "agent label resolved")
    _expect(any(e["phase"] == "result" and e["agent"] == "wallbit" for e in steps), "result step for wallbit")
    _expect([t["text"] for t in tokens] == answer_tokens, "answer streamed token-by-token")
    _expect("".join(t["text"] for t in tokens) == finals[0]["text"], "tokens concatenate to the final text")
    _expect(finals[0]["text"] == "Tienes 100 USD disponibles.", "final text = last AIMessage content")
    _expect(finals[0]["pending_confirmation"] is None, "no pending confirmation")

    # The token filter must ignore tool-call chunks and nested-subagent tokens.
    from langchain_core.messages import AIMessageChunk
    _expect(chat_stream._answer_token((AIMessageChunk(content="hola"), {"langgraph_node": "model", "langgraph_checkpoint_ns": "model:1"})) == "hola", "_answer_token accepts a depth-0 model token")
    _expect(chat_stream._answer_token((AIMessageChunk(content="leak"), {"langgraph_node": "model", "langgraph_checkpoint_ns": "tools:1|model:2"})) is None, "_answer_token rejects a nested-subagent token")
    _expect(chat_stream._answer_token((AIMessageChunk(content="x", tool_calls=[{"name": "t", "args": {}, "id": "1", "type": "tool_call"}]), {"langgraph_node": "model", "langgraph_checkpoint_ns": "model:1"})) is None, "_answer_token rejects a tool-call chunk")

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
                yield ("updates", update)

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

    print("\n[direct specialist: wallbit] streams its OWN tool calls + pending")
    specialist_updates = [
        {"model": {"messages": [AIMessage(
            content="",
            tool_calls=[{"name": "wallbit_place_trade", "args": {"action": "BUY", "symbol": "AAPL", "amount_usd": 20}, "id": "9", "type": "tool_call"}],
        )]}},
        {"tools": {"messages": [ToolMessage(content="preview generado", name="wallbit_place_trade", tool_call_id="9")]}},
        {"model": {"messages": [AIMessage(content="Listo, confirma la compra.")]}},
    ]
    spec_preview = {"requires_confirmation": True, "confirmation_id": 7, "preview": {"summary": "BUY AAPL 20 USD"}}
    chat_stream.build_wallbit_subagent = lambda user, channel, msg: _FakeSupervisor(specialist_updates)  # type: ignore[assignment]
    chat_stream.extract_pending_confirmation = lambda msgs: spec_preview  # type: ignore[assignment]
    spec_events = _collect_single(chat_stream, user, "wallbit", "compra 20 de apple")
    spec_steps = [e for e in spec_events if e["type"] == "step"]
    spec_final = next(e for e in spec_events if e["type"] == "final")
    _expect(bool(spec_steps) and spec_steps[0]["phase"] == "delegate" and spec_steps[0]["agent"] == "wallbit", "first step = the agent's own tool call")
    _expect(spec_steps[0]["label"] == "Proponer operación", "tool name humanized")
    _expect("action: BUY" in spec_steps[0]["instruction"], "tool args summarized")
    _expect(any(e["phase"] == "result" for e in spec_steps), "tool result step present")
    _expect(spec_final["text"] == "Listo, confirma la compra.", "specialist final text")
    _expect(spec_final["pending_confirmation"] == spec_preview, "wallbit pending surfaced in direct chat")

    print("\n[risk profile] direct chat runs the read-only risk agent (not the supervisor)")
    risk_updates = [
        {"model": {"messages": [AIMessage(
            content="",
            tool_calls=[{"name": "get_risk_profile_status", "args": {}, "id": "r1", "type": "tool_call"}],
        )]}},
        {"tools": {"messages": [ToolMessage(content='{"declarado": {"tolerancia": "conservador"}}', name="get_risk_profile_status", tool_call_id="r1")]}},
        {"model": {"messages": [AIMessage(content="Tu perfil declarado es conservador.")]}},
    ]
    chat_stream.build_risk_subagent = lambda user: _FakeSupervisor(risk_updates)  # type: ignore[assignment]
    risk_events = _collect_single(chat_stream, user, "risk", "ya tengo perfil?")
    risk_steps = [e for e in risk_events if e["type"] == "step"]
    risk_final = next(e for e in risk_events if e["type"] == "final")
    _expect(bool(risk_steps) and risk_steps[0]["phase"] == "delegate", "risk agent runs its read tool")
    _expect(risk_steps[0]["label"] == "Leer tu perfil", "risk tool name humanized")
    _expect(risk_final["text"] == "Tu perfil declarado es conservador.", "risk agent final text")
    _expect(risk_final["pending_confirmation"] is None, "risk chat has no pending confirmation")

    print("\n[roster] team directory shape")
    from agents.agent_directory import AGENTS, get_agent

    ids = [a["id"] for a in AGENTS]
    _expect(ids == ["tresqu", "expenses", "wallbit", "analyst", "risk"], "roster ids + order")
    _expect(get_agent("wallbit") is not None and get_agent("wallbit").get("requires_wallbit") is True, "wallbit requires a connected account")
    _expect(get_agent("wallbit").get("real_money") is None, "wallbit real-money flag removed")
    _expect(get_agent("analyst").get("data_sources") == ["Perfil de riesgo", "Portafolio de Wallbit"], "analyst data sources for honest viz")
    _expect(get_agent("analyst").get("prefers_wallbit") is True, "analyst prefers (but doesn't require) wallbit")
    _expect(get_agent("risk").get("start_command") == "/perfil", "risk profiler start command exposed")
    _expect(get_agent("nope") is None, "unknown agent → None")

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
