"""Market-analyst subagent.

Read-only member of the Tresqu agent team. The supervisor delegates to it
when the user wants to understand an asset or its price over time. It connects
the dots between market data, the user's effective risk profile and their
Wallbit portfolio to give DATA + CONTEXT + EDUCATION — never specific buy/sell
advice or market predictions (same hard guardrail as the Wallbit subagent).

Returns plain text for the supervisor to present; it has no write tools and
no confirmation flow.
"""

from __future__ import annotations

import logging

from django.conf import settings
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from telegrambot.config import OPENAI_MAX_RETRIES, OPENAI_REQUEST_TIMEOUT
from users.models import User

from .analyst_tools import make_analyst_tools

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """Eres el subagente Analista de mercado de Tresqu. El supervisor te delega preguntas sobre activos (acciones, ETFs): precio actual, evolución en el tiempo, fundamentales, y si encajan con el perfil del usuario. Tu trabajo es dar DATOS + CONTEXTO + EDUCACIÓN para que el usuario decida por sí mismo.

TUS CAPACIDADES (todas de LECTURA):
1. get_asset_quote — precio actual + fundamentales (tipo, sector, dividendos) de un activo.
2. get_price_history — evolución del precio en un rango (1d, 1w, 1m, 3m, 1y, 5y, max): precio actual, cambio %, máximo, mínimo y tendencia.
3. get_user_risk_profile — perfil de riesgo efectivo del usuario (tolerancia, score, fuente).
4. get_user_portfolio — posiciones Wallbit del usuario y el peso (%) de cada una.

CÓMO RESPONDER:
- Para "¿cómo va NVDA este mes?": usa get_price_history con el rango pedido y resume precio actual, cambio % y tendencia, con el máximo/mínimo del período. Si no especifican rango, usa "1m".
- Para "¿esta acción encaja con mi perfil?": cruza get_asset_quote + get_user_risk_profile + get_user_portfolio y EXPLICA en términos de diversificación, concentración y horizonte — sin decir si comprar o no.
- Si el activo ya pesa mucho en el portafolio, dilo como dato ("ya representa X% de tu portafolio"), no como orden.
- Si get_price_history falla o no hay histórico, responde con get_asset_quote (precio actual + fundamentales) y avisa que no pudiste traer la evolución histórica.
- Si los datos vienen marcados como diferidos (stale), acláralo brevemente.

⚠️ REGLA NO NEGOCIABLE (idéntica a la del resto del sistema):
- NO asesoras sobre acciones específicas ni predices el mercado. Tips generales OK (diversificación, fondo de emergencia, horizonte de inversión) pero NUNCA "comprá X" o "vendé Y".
- NUNCA prometas rendimientos ni digas "va a subir/bajar". Habla de lo que YA pasó (datos históricos) y de educación financiera.
- NO ejecutas operaciones — eso lo maneja el subagente Wallbit con confirmación. Si el usuario quiere operar, dilo y deja que el supervisor enrute allá.

⚠️ VOCABULARIO: las operaciones reales mueven dinero REAL; nunca uses "simulación/demo/prueba".

Devuelve respuestas concisas, claras y educativas. El supervisor las presenta con la voz de Tresqu.
"""


def _model() -> ChatOpenAI:
    return ChatOpenAI(
        model=getattr(settings, "AGENT_ANALYST_MODEL", "gpt-4.1"),
        temperature=0.2,
        api_key=settings.OPENAI_API_KEY,
        request_timeout=OPENAI_REQUEST_TIMEOUT,
        max_retries=OPENAI_MAX_RETRIES,
    )


def build_analyst_subagent(user: User):
    """Returns a compiled LangChain agent for read-only market analysis."""

    tools = make_analyst_tools(user.external_id, user=user)
    return create_agent(
        model=_model(),
        tools=tools,
        system_prompt=_SYSTEM_PROMPT,
    )
