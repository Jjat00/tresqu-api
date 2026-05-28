"""Risk-profile agent: a read-only conversational helper.

Reads the user's declared (Q&A) and inferred (records) risk signals plus the
effective profile, explains what drives them, and can recompute the inference
on demand. It NEVER sets the profile: the declared profile is only updated via
the guided questionnaire (started from chat with ``/perfil`` or the
"Actualizar perfil" button), which produces an auditable ``RiskAssessment`` that
gates real-money operations.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from telegrambot.config import OPENAI_MAX_RETRIES, OPENAI_REQUEST_TIMEOUT
from users.models import User

from ..effective_profile import get_effective_profile

logger = logging.getLogger(__name__)

_TOLERANCE_ES = {
    "conservative": "conservador",
    "moderate": "moderado",
    "aggressive": "agresivo",
}

_DIMENSION_ES = {
    "savings_rate": "tasa de ahorro",
    "income_stability": "estabilidad de ingresos",
    "expense_stability": "estabilidad de gastos",
    "holdings_appetite": "apetito por inversiones",
    "liquidity_buffer": "colchón de liquidez",
}

_SYSTEM_PROMPT = """Eres el agente de Perfil de riesgo de Tresqu. Ayudas al usuario a entender y mejorar su perfil de inversión para que sea lo más fiel posible a su situación real.

HAY DOS SEÑALES (y una combinación):
- Declarado (por cuestionario): lo que el usuario respondió en el Q&A.
- Inferido (de sus registros): estimado automáticamente de sus gastos, ingresos y operaciones.
- Efectivo: la combinación que el sistema USA de verdad (puede aplicar un tope más prudente que el declarado).

TOOLS (solo lectura):
- get_risk_profile_status: léela SIEMPRE antes de hablar del perfil. Trae el declarado, el inferido y el efectivo.
- recompute_inference: úsala si el usuario registró movimientos hace poco o pide actualizar/recalcular su inferencia.

CÓMO RESPONDER:
- Si te saludan o preguntan "cómo vas", responde breve y natural y ofrece ayudar con el perfil; NO recites el perfil completo si no lo piden.
- Si preguntan si ya tienen perfil o cuál es: usa get_risk_profile_status y di claramente el declarado y el inferido (si existen) y cuál es el efectivo, con una frase de por qué.
- Si el declarado y el inferido difieren, explícalo con calma (p.ej. el sistema aplica el más prudente como protección).
- Para que el perfil sea más fiel: invita a (re)hacer el cuestionario y a registrar con juicio sus movimientos (más y mejores registros → mejor inferencia).

LÍMITES:
- NO fijas ni cambias el perfil tú mismo. El perfil declarado SOLO se actualiza con el cuestionario guiado: dile al usuario que toque *Actualizar perfil* / *Iniciar evaluación* o que escriba /perfil.
- NO inventes tolerancias, scores ni dimensiones: usa solo lo que devuelven las tools.
- Responde en español, conciso y claro. Usa *negrita* con asterisco. Nunca uses "simulación/demo/prueba".
"""


def _model() -> ChatOpenAI:
    return ChatOpenAI(
        model=getattr(settings, "AGENT_ANALYST_MODEL", "gpt-4.1"),
        temperature=0.2,
        api_key=settings.OPENAI_API_KEY,
        request_timeout=OPENAI_REQUEST_TIMEOUT,
        max_retries=OPENAI_MAX_RETRIES,
    )


def _es_tolerances(text: str | None) -> str | None:
    if not text:
        return text
    for en, es in _TOLERANCE_ES.items():
        text = text.replace(en, es)
    return text


def _assessment_es(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    dims = snapshot.get("dimensions") or {}
    return {
        "tolerancia": _TOLERANCE_ES.get(snapshot.get("tolerance"), snapshot.get("tolerance")),
        "score": snapshot.get("score"),
        "confianza": snapshot.get("confidence"),
        "dimensiones": {_DIMENSION_ES.get(k, k): v for k, v in dims.items()},
        "registrado": snapshot.get("recorded_at"),
    }


def _profile_payload(user: User, *, refresh: bool) -> dict[str, Any]:
    eff = get_effective_profile(
        user,
        refresh_inference=refresh,
        max_age_days=0 if refresh else 7,
    )
    return {
        "tiene_perfil_declarado": eff.declared is not None,
        "tiene_perfil_inferido": eff.inferred is not None,
        "declarado": _assessment_es(eff.declared),
        "inferido": _assessment_es(eff.inferred),
        "efectivo": {
            "tolerancia": _TOLERANCE_ES.get(eff.tolerance, eff.tolerance),
            "score": eff.score,
            "confianza": round(eff.confidence, 2),
            "fuente": eff.source,
            "nota": _es_tolerances(eff.warning),
        },
    }


def build_risk_subagent(user: User):
    """Returns a compiled LangChain agent for the read-only risk-profile helper."""

    @tool("get_risk_profile_status")
    async def get_risk_profile_status() -> str:
        """Lee el perfil de riesgo actual del usuario: el declarado (por cuestionario), el inferido (de sus registros) y el efectivo (el que el sistema usa). Úsala antes de hablar del perfil."""
        try:
            data = await sync_to_async(_profile_payload)(user, refresh=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception("get_risk_profile_status failed: %s", exc)
            return "(no se pudo leer el perfil ahora mismo)"
        return json.dumps(data, ensure_ascii=False)

    @tool("recompute_inference")
    async def recompute_inference() -> str:
        """Recalcula al instante el perfil inferido a partir de los registros actuales del usuario (gastos, ingresos, operaciones). Úsala si el usuario registró movimientos recientemente o pide actualizar su inferencia."""
        try:
            data = await sync_to_async(_profile_payload)(user, refresh=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("recompute_inference failed: %s", exc)
            return "(no se pudo recalcular la inferencia ahora mismo)"
        return json.dumps(data, ensure_ascii=False)

    return create_agent(
        model=_model(),
        tools=[get_risk_profile_status, recompute_inference],
        system_prompt=_SYSTEM_PROMPT,
    )
