"""Risk Profiler — multi-turn Q&A that builds the user's investment risk profile.

A ``StateGraph`` with a 5-question Q&A loop. Each ``ask_question`` node
emits an ``interrupt`` so the graph pauses; the next user message resumes
the graph via ``Command(resume=...)``. The final ``synthesize_profile``
node uses a single LLM call to consolidate raw answers into a structured
profile, which ``persist`` stores in ``RiskProfile`` + ``RiskAssessment``.

Compiled with ``AsyncPostgresSaver`` so state survives between turns.
Thread id is keyed per user — only one active session per user at a time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Literal, TypedDict

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from telegrambot.config import OPENAI_MAX_RETRIES, OPENAI_REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


# Hard-coded Q&A in user-facing copy. Keys match dimension names in
# ``RiskProfile.dimensions``. ``closing`` is a forced-choice scenario that
# cross-cuts dimensions and helps disambiguate edge cases.
QUESTIONS: list[dict[str, str]] = [
    {
        "key": "intro",
        "text": (
            "Vamos a armar tu perfil de inversión 🎯\n"
            "Te haré 5 preguntas cortas. Responde con naturalidad — no hay "
            "respuestas correctas o incorrectas. Cuando termines, te muestro "
            "tu perfil y queda guardado.\n\n"
            "Para empezar: ¿en qué plazo planeas usar el dinero que inviertes? "
            "¿Menos de 1 año, entre 3 y 5 años, o más de 10 años?"
        ),
    },
    {
        "key": "loss_tolerance",
        "text": (
            "Imagina que invertiste lo que tienes ahorrado y, a los 6 meses, tu "
            "portafolio cayó un 20%. ¿Qué harías?\n"
            "a) Vendo todo para no perder más\n"
            "b) Vendo una parte para limitar el daño\n"
            "c) Mantengo y espero recuperación\n"
            "d) Compro más porque ahora está más barato"
        ),
    },
    {
        "key": "liquidity_need",
        "text": (
            "¿Qué porcentaje de tu ahorro necesitas mantener líquido (con acceso "
            "inmediato, sin tener que vender inversiones)? Aproximadamente."
        ),
    },
    {
        "key": "knowledge_level",
        "text": (
            "Del 1 al 10, ¿qué tan familiar te sientes con instrumentos como "
            "ETFs, bonos, acciones individuales, fondos? 1 = nunca los he usado, "
            "10 = los uso/entiendo a diario."
        ),
    },
    {
        "key": "closing",
        "text": (
            "Última: te ofrecen dos opciones —\n"
            "A) Recibir 5M garantizados ahora mismo\n"
            "B) 50% de probabilidad de ganar 12M, 50% de no ganar nada\n"
            "¿Cuál eliges y por qué?"
        ),
    },
]


class RiskProfilerState(TypedDict, total=False):
    """State carried between turns of the Q&A.

    Persisted via the checkpointer — every field here survives the pause
    at ``interrupt()`` boundaries.
    """

    user_id: int
    channel: str
    current_idx: int
    answers: dict[str, str]
    context: dict[str, Any]
    profile_draft: dict[str, Any] | None
    final_text: str
    status: Literal["in_progress", "completed", "abandoned"]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def _load_context_node(state: RiskProfilerState) -> dict[str, Any]:
    """Load cheap context signals so synthesize_profile can ground its answer.

    Currently just notes recent activity. Future sub-phases (1.3) will
    expand this with savings_rate, portfolio composition, volatility.
    """

    from expenses.models import Expense
    from income.models import Income

    @sync_to_async
    def _gather() -> dict[str, Any]:
        ninety_days_ago = (timezone.now() - timedelta(days=90)).date()
        expenses_count = Expense.objects.filter(
            user_id=state["user_id"], spent_at__gte=ninety_days_ago
        ).count()
        incomes_count = Income.objects.filter(
            user_id=state["user_id"], received_at__gte=ninety_days_ago
        ).count()
        return {
            "recent_expenses_count": expenses_count,
            "recent_incomes_count": incomes_count,
            "loaded_at": timezone.now().isoformat(),
        }

    return {
        "context": await _gather(),
        "current_idx": 0,
        "answers": {},
        "status": "in_progress",
    }


def _ask_question_node(state: RiskProfilerState) -> dict[str, Any]:
    """Pause the graph until the user answers the current question.

    The interrupt payload is what the bot will surface to the user; the
    resume value is the user's raw text answer.
    """

    idx = state.get("current_idx", 0)
    question = QUESTIONS[idx]
    answer = interrupt({"question": question["text"], "step": idx + 1, "total": len(QUESTIONS)})
    answers = dict(state.get("answers", {}))
    answers[question["key"]] = str(answer).strip()
    return {"answers": answers, "current_idx": idx + 1}


def _route_after_answer(state: RiskProfilerState) -> Literal["ask_question", "synthesize_profile"]:
    if state.get("current_idx", 0) >= len(QUESTIONS):
        return "synthesize_profile"
    return "ask_question"


_SYNTH_SYSTEM_PROMPT = """Eres un analista financiero que arma perfiles de tolerancia al riesgo de inversión.

Recibes 5 respuestas libres a un Q&A fijo. Devuelve un JSON estricto con este shape:

{
  "tolerance": "conservative" | "moderate" | "aggressive",
  "score": <int 0-100>,
  "dimensions": {
    "time_horizon": <int 0-100>,
    "loss_tolerance": <int 0-100>,
    "liquidity_need": <int 0-100>,
    "knowledge_level": <int 0-100>
  },
  "confidence": <float 0.0-1.0>,
  "reason": "<2-3 frases en español explicando el perfil>"
}

REGLAS DE SCORING (0=mínimo, 100=máximo):
- time_horizon: 0 = necesita el dinero en <1 año; 100 = >10 años sin tocar
- loss_tolerance: 0 = vende todo ante 20% de caída; 100 = compra más en la caída
- liquidity_need: 0 = necesita 100% líquido; 100 = no necesita liquidez (puede inmovilizar todo)
  NOTA: liquidity_need ALTO favorece tolerancia al riesgo (puede inmovilizar capital).
- knowledge_level: 0 = nunca usó instrumentos; 100 = los maneja a diario

MAPEO score → tolerance:
- 0-35  → conservative
- 36-65 → moderate
- 66-100 → aggressive

CONFIDENCE:
- 0.9+ si las 5 respuestas son consistentes entre sí
- 0.6-0.8 si hay tensión entre 2-3 respuestas
- <0.6 si hay contradicciones fuertes

REGLAS:
- score debe ser un promedio ponderado coherente con las dimensiones.
- reason en español, 2-3 frases, sin tecnicismos vacíos.
- Responde SOLO con el JSON, sin markdown, sin código de bloque, sin comentarios."""


async def _synthesize_profile_node(state: RiskProfilerState) -> dict[str, Any]:
    """Single LLM call that consolidates raw answers into a structured profile."""

    answers = state.get("answers", {})
    answers_block = "\n\n".join(
        f"Pregunta ({q['key']}): {q['text']}\nRespuesta del usuario: {answers.get(q['key'], '(sin responder)')}"
        for q in QUESTIONS
    )

    llm = ChatOpenAI(
        model=getattr(settings, "AGENT_RISK_PROFILER_MODEL", "gpt-4.1"),
        temperature=0.2,
        api_key=settings.OPENAI_API_KEY,
        request_timeout=OPENAI_REQUEST_TIMEOUT,
        max_retries=OPENAI_MAX_RETRIES,
        model_kwargs={"response_format": {"type": "json_object"}},
    )

    response = await llm.ainvoke(
        [
            SystemMessage(content=_SYNTH_SYSTEM_PROMPT),
            HumanMessage(content=answers_block),
        ]
    )

    try:
        draft = json.loads(response.content)
    except json.JSONDecodeError as exc:
        logger.error(f"risk profiler synthesis returned invalid JSON: {exc}; raw={response.content!r}")
        draft = {
            "tolerance": "moderate",
            "score": 50,
            "dimensions": {
                "time_horizon": 50,
                "loss_tolerance": 50,
                "liquidity_need": 50,
                "knowledge_level": 50,
            },
            "confidence": 0.3,
            "reason": "No fue posible procesar las respuestas con suficiente claridad. Perfil moderado por defecto.",
        }

    return {"profile_draft": draft}


async def _persist_node(state: RiskProfilerState) -> dict[str, Any]:
    """Save the synthesized profile to ``RiskProfile`` + ``RiskAssessment``."""

    from agents.models import RiskAssessment, RiskProfile
    from users.models import User

    draft = state.get("profile_draft") or {}
    context = state.get("context") or {}

    @sync_to_async
    def _save() -> str:
        user = User.objects.get(id=state["user_id"])
        profile, _ = RiskProfile.objects.update_or_create(
            user=user,
            defaults={
                "tolerance": draft.get("tolerance", "moderate"),
                "score": int(draft.get("score", 50)),
                "dimensions": draft.get("dimensions", {}),
                "confidence": float(draft.get("confidence", 0.5)),
                "user_override": False,
                "derived_from": {
                    "method": "chat_qa",
                    "channel": state.get("channel", "unknown"),
                    "completed_at": timezone.now().isoformat(),
                },
                "notes": draft.get("reason", ""),
            },
        )
        RiskAssessment.objects.create(
            profile=profile,
            tolerance=profile.tolerance,
            score=profile.score,
            dimensions=profile.dimensions,
            confidence=profile.confidence,
            reason=draft.get("reason", ""),
            triggered_by=RiskAssessment.CHAT_QA,
            context_snapshot={"answers": state.get("answers", {}), "context": context},
        )
        return _format_completion_text(draft)

    final_text = await _save()
    return {"status": "completed", "final_text": final_text}


def _format_completion_text(draft: dict[str, Any]) -> str:
    """User-facing recap shown after persistence."""

    tolerance_label = {
        "conservative": "Conservador",
        "moderate": "Moderado",
        "aggressive": "Agresivo",
    }.get(draft.get("tolerance", "moderate"), "Moderado")

    score = draft.get("score", 50)
    confidence_pct = int(round(float(draft.get("confidence", 0.5)) * 100))
    reason = draft.get("reason", "")

    return (
        f"✅ *Tu perfil de inversión está listo*\n\n"
        f"• Tolerancia: *{tolerance_label}*\n"
        f"• Score: *{score}/100*\n"
        f"• Confianza: _{confidence_pct}%_\n\n"
        f"{reason}\n\n"
        f"Puedes verlo y ajustarlo cuando quieras en https://tresqu.com/profile"
    )


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def _build_graph_uncompiled() -> StateGraph:
    builder = StateGraph(RiskProfilerState)
    builder.add_node("load_context", _load_context_node)
    builder.add_node("ask_question", _ask_question_node)
    builder.add_node("synthesize_profile", _synthesize_profile_node)
    builder.add_node("persist", _persist_node)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "ask_question")
    builder.add_conditional_edges("ask_question", _route_after_answer)
    builder.add_edge("synthesize_profile", "persist")
    builder.add_edge("persist", END)
    return builder


def compile_risk_profiler_graph(checkpointer):
    """Compile the graph with the given (async) checkpointer."""

    return _build_graph_uncompiled().compile(checkpointer=checkpointer)


def thread_id_for(user_id: int) -> str:
    return f"risk_profiler:{user_id}"


SESSION_TIMEOUT = timedelta(hours=24)


def is_session_stale(loaded_at_iso: str | None) -> bool:
    """Returns True if a paused session is older than ``SESSION_TIMEOUT``."""

    if not loaded_at_iso:
        return False
    try:
        loaded_at = datetime.fromisoformat(loaded_at_iso)
    except ValueError:
        return False
    if loaded_at.tzinfo is None:
        return False
    return (timezone.now() - loaded_at) > SESSION_TIMEOUT
