"""Canonical directory of the Tresqu agent team.

Single source of truth for (a) the roster shown in the web "Agentes" module and
(b) the supervisor-tool → agent-id/label mapping used by the live trace. The
``kind`` of each agent drives the UI behaviour:

- ``supervisor``  → Tresqu: the orchestrator chat with the live agent graph.
- ``specialist``  → a direct 1:1 chat that streams the agent's own tool calls.
- ``profiler``    → launches the guided risk Q&A instead of a free chat.

Important: the team is strictly hub-and-spoke. Specialists never talk to each
other; the only cross-domain reads are the Analyst's ``data_sources`` (it reads
the risk profile and Wallbit portfolio via its own tools, not as agent-to-agent
messages). The roster exposes ``data_sources`` so the UI can show those reads
honestly as dotted inputs rather than implying lateral collaboration.
"""

from __future__ import annotations

from .services import RISK_PROFILE_COMMAND

SUPERVISOR_ID = "tresqu"

AGENTS: list[dict] = [
    {
        "id": SUPERVISOR_ID,
        "label": "Tresqu",
        "kind": "supervisor",
        "tool_name": None,
        "specialty": "Coordina al equipo: entiende lo que pides y lo deriva al especialista correcto.",
        "capabilities": [
            "Conversa de todo lo financiero en un solo lugar",
            "Delega a Gastos, Wallbit, Analista o Perfil de riesgo",
            "Combina respuestas de varios especialistas en un mismo turno",
        ],
    },
    {
        "id": "expenses",
        "label": "Gastos e ingresos",
        "kind": "specialist",
        "tool_name": "manage_expenses_and_income",
        "specialty": "Registra, edita, elimina y consulta tus gastos e ingresos, con resúmenes y categorías.",
        "capabilities": [
            "Registrar un gasto o ingreso",
            "Resumen del mes o por categoría",
            "Editar o borrar movimientos",
        ],
    },
    {
        "id": "wallbit",
        "label": "Wallbit",
        "kind": "specialist",
        "tool_name": "manage_wallbit",
        "specialty": "Tu cuenta Wallbit: saldo, movimientos y operaciones reales (con confirmación previa).",
        "capabilities": [
            "Consultar saldo y movimientos",
            "Comprar o vender (requiere confirmar)",
            "Mover fondos, Robo Advisor y tarjeta",
        ],
        # Hard requirement: without a connected Wallbit account this agent has
        # nothing to read or operate on, so the UI locks it behind a connect CTA.
        "requires_wallbit": True,
    },
    {
        "id": "analyst",
        "label": "Analista",
        "kind": "specialist",
        "tool_name": "analyze_investment",
        "specialty": "Análisis de activos solo lectura y educativo. No recomienda comprar ni vender.",
        "capabilities": [
            "Precio de un activo en el tiempo",
            "Explicar una acción o ETF",
            "Si un activo encaja con tu perfil",
        ],
        # Honest visualization: data the Analyst READS, not agent-to-agent msgs.
        "data_sources": ["Perfil de riesgo", "Portafolio de Wallbit"],
        # Soft dependency: it works without Wallbit (prices, fundamentals, ETFs)
        # but connecting it lets the analysis weigh your actual portfolio.
        "prefers_wallbit": True,
    },
    {
        "id": "risk",
        "label": "Perfil de riesgo",
        "kind": "profiler",
        "tool_name": "start_risk_profiler",
        "specialty": "Evalúa tu tolerancia al riesgo con un cuestionario corto de 5 preguntas.",
        "capabilities": [
            "Cuestionario guiado de 5 preguntas",
            "Calcula tu tolerancia y la guarda",
        ],
        "start_command": RISK_PROFILE_COMMAND,
        "note": (
            "Tu perfil también se infiere automáticamente a partir de tus registros. "
            "Entre más y mejor registres tus gastos, ingresos y operaciones, más "
            "preciso será — sé juicioso con tus registros."
        ),
    },
]

_BY_ID: dict[str, dict] = {a["id"]: a for a in AGENTS}
_BY_TOOL: dict[str, dict] = {a["tool_name"]: a for a in AGENTS if a.get("tool_name")}

SPECIALIST_IDS: tuple[str, ...] = tuple(
    a["id"] for a in AGENTS if a["kind"] == "specialist"
)


def get_agent(agent_id: str) -> dict | None:
    return _BY_ID.get(agent_id)


def agent_meta_by_tool(tool_name: str) -> dict[str, str]:
    """Supervisor tool name → ``{id, label}`` for the live agent graph."""
    agent = _BY_TOOL.get(tool_name)
    if agent:
        return {"id": agent["id"], "label": agent["label"]}
    return {"id": tool_name or "unknown", "label": tool_name or "Agente"}
