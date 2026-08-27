"""Expenses & Income subagent.

Owns every tool related to personal-finance bookkeeping for the user:
parsing, classification, CRUD and analytics on expenses and incomes.

The supervisor invokes it as a single ``@tool`` with a natural-language
instruction. Categories context is injected into the subagent's own
system prompt so the supervisor does not need to forward it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from django.conf import settings
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from telegrambot.config import OPENAI_MAX_RETRIES, OPENAI_REQUEST_TIMEOUT
from telegrambot.tools import (
    create_expense,
    create_income,
    delete_expense,
    delete_income,
    get_current_date,
    get_expense_by_id,
    get_expense_totals,
    get_expenses_by_category,
    get_expenses_by_user,
    get_income_by_id,
    get_income_totals,
    get_incomes_by_category,
    get_incomes_by_user,
    get_monthly_insights,
    get_or_create_category,
    get_or_create_income_category,
    get_top_categories,
    get_top_income_categories,
    is_greeting,
    parse_expense,
    parse_expenses,
    parse_income,
    parse_incomes,
    parse_relative_date,
    search_expenses_by_amount,
    search_expenses_by_text,
    search_incomes_by_amount,
    search_incomes_by_text,
    update_expense,
    update_income,
)
from users.models import User

logger = logging.getLogger(__name__)


def _model() -> ChatOpenAI:
    return ChatOpenAI(
        model=getattr(settings, "AGENT_EXPENSES_MODEL", "gpt-4.1"),
        temperature=0.1,
        api_key=settings.OPENAI_API_KEY,
        request_timeout=OPENAI_REQUEST_TIMEOUT,
        max_retries=OPENAI_MAX_RETRIES,
    )


def build_expenses_tools(
    user: User,
    expense_categories_str: str = "",
    income_categories_str: str = "",
) -> list:
    """All tools the expenses subagent needs, with ``user_external_id`` bound.

    Las categorías existentes del usuario se pasan a los parsers para que
    clasifiquen reutilizando una categoría existente en vez de inventar una nueva
    por cada gasto/ingreso.
    """

    external_id = user.external_id
    default_currency = user.default_currency or "USD"

    @tool
    async def parse_expense_for_user(text: str) -> dict:
        """Analiza un mensaje y extrae UN gasto (monto, categoría, fecha, nota).
        Interpreta montos coloquiales según la moneda por defecto del usuario."""
        return await parse_expense.ainvoke({
            "text": text,
            "user_default_currency": default_currency,
            "expense_categories": expense_categories_str,
        })

    @tool
    async def parse_expenses_for_user(text: str) -> Dict[str, Any]:
        """Extrae VARIOS gastos de un mismo mensaje. Interpreta montos coloquiales
        según la moneda por defecto del usuario."""
        return await parse_expenses.ainvoke({
            "text": text,
            "user_default_currency": default_currency,
            "expense_categories": expense_categories_str,
        })

    @tool
    async def parse_income_for_user(text: str) -> dict:
        """Analiza un mensaje y extrae UN ingreso (monto, categoría, fecha, nota).
        Interpreta montos coloquiales según la moneda por defecto del usuario."""
        return await parse_income.ainvoke({
            "text": text,
            "user_default_currency": default_currency,
            "income_categories": income_categories_str,
        })

    @tool
    async def parse_incomes_for_user(text: str) -> Dict[str, Any]:
        """Extrae VARIOS ingresos de un mismo mensaje. Interpreta montos coloquiales
        según la moneda por defecto del usuario."""
        return await parse_incomes.ainvoke({
            "text": text,
            "user_default_currency": default_currency,
            "income_categories": income_categories_str,
        })

    @tool
    def get_current_date_for_user() -> str:
        """Obtiene la fecha actual en formato YYYY-MM-DD considerando la zona horaria del usuario."""
        try:
            return get_current_date.invoke({"user_external_id": external_id})
        except Exception as exc:
            logger.error(f"get_current_date_for_user: {exc}")
            return datetime.now().strftime("%Y-%m-%d")

    @tool
    def parse_relative_date_for_user(date_text: str) -> str:
        """Convierte referencias temporales relativas a fechas YYYY-MM-DD."""
        try:
            return parse_relative_date.invoke({
                "date_text": date_text,
                "user_external_id": external_id,
            })
        except Exception as exc:
            logger.error(f"parse_relative_date_for_user: {exc}")
            return datetime.now().strftime("%Y-%m-%d")

    @tool
    def create_expense_for_user(
        amount: float,
        category: str,
        currency: str = "",
        spent_at: str | None = None,
        note: str | None = "",
    ) -> str:
        """Registra un gasto del usuario. Deja `currency` vacío si el usuario no la dijo
        explícitamente (NO la infieras): la tool usará la moneda por defecto del usuario."""
        return create_expense.invoke({
            "user_external_id": external_id,
            "amount": amount,
            "currency": currency,
            "category": category,
            "spent_at": spent_at,
            "note": note,
        })

    @tool
    def create_income_for_user(
        amount: float,
        category: str,
        currency: str = "",
        received_at: str | None = None,
        note: str | None = "",
        category_description: str | None = None,
        category_example: str | None = None,
        category_color: str | None = None,
    ) -> str:
        """Registra un ingreso del usuario. Si la categoría no existe, la crea. Deja `currency`
        vacío si el usuario no la dijo explícitamente (NO la infieras): la tool usará la moneda
        por defecto del usuario."""
        get_or_create_income_category.invoke({
            "name": category,
            "description": category_description,
            "example": category_example,
            "color": category_color,
        })
        return create_income.invoke({
            "user_external_id": external_id,
            "amount": amount,
            "currency": currency,
            "category": category,
            "received_at": received_at,
            "note": note,
        })

    @tool
    def get_expense_totals_for_user(start_date: str | None = None, end_date: str | None = None) -> Dict[str, Any]:
        """Total EXACTO de gastos en un período (YYYY-MM-DD, inclusive), por moneda,
        calculado en base de datos con el mismo criterio que el dashboard. Úsala
        SIEMPRE para "cuánto gasté", "cuánto llevo este mes", "total de gastos"."""
        try:
            return get_expense_totals.invoke({
                "user_external_id": external_id,
                "start_date": start_date,
                "end_date": end_date,
            })
        except Exception as exc:
            logger.error(f"get_expense_totals_for_user: {exc}")
            return {"error": str(exc)}

    @tool
    def get_income_totals_for_user(start_date: str | None = None, end_date: str | None = None) -> Dict[str, Any]:
        """Total EXACTO de ingresos en un período (YYYY-MM-DD, inclusive), por moneda,
        calculado en base de datos. Úsala SIEMPRE para "cuánto ingresé / recibí"."""
        try:
            return get_income_totals.invoke({
                "user_external_id": external_id,
                "start_date": start_date,
                "end_date": end_date,
            })
        except Exception as exc:
            logger.error(f"get_income_totals_for_user: {exc}")
            return {"error": str(exc)}

    @tool
    def get_user_expenses(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
        """Lista los gastos del usuario en un rango de fechas (máx. 300, más recientes
        primero). Para consultas de período pasa SIEMPRE start_date y end_date.
        NO la uses para calcular totales: usa get_expense_totals_for_user."""
        try:
            return get_expenses_by_user.invoke({
                "user_external_id": external_id,
                "start_date": start_date,
                "end_date": end_date,
            })
        except Exception as exc:
            logger.error(f"get_user_expenses: {exc}")
            return []

    @tool
    def get_user_incomes(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
        """Lista los ingresos del usuario en un rango de fechas. Para consultas de
        período pasa SIEMPRE start_date y end_date. NO la uses para totales: usa
        get_income_totals_for_user."""
        try:
            return get_incomes_by_user.invoke({
                "user_external_id": external_id,
                "start_date": start_date,
                "end_date": end_date,
            })
        except Exception as exc:
            logger.error(f"get_user_incomes: {exc}")
            return []

    @tool
    def search_expenses(search_text: str) -> List[Dict[str, Any]]:
        """Búsqueda semántica de gastos por texto libre (usa embeddings)."""
        try:
            return search_expenses_by_text.invoke({
                "user_external_id": external_id,
                "search_text": search_text,
            })
        except Exception as exc:
            logger.error(f"search_expenses: {exc}")
            return []

    @tool
    def search_incomes(search_text: str) -> List[Dict[str, Any]]:
        """Búsqueda semántica de ingresos por texto libre (usa embeddings)."""
        try:
            return search_incomes_by_text.invoke({
                "user_external_id": external_id,
                "search_text": search_text,
            })
        except Exception as exc:
            logger.error(f"search_incomes: {exc}")
            return []

    @tool
    def search_expenses_by_exact_amount(amount: float, currency: str | None = None) -> List[Dict[str, Any]]:
        """Busca gastos por monto exacto (los más recientes primero). Úsala cuando el
        usuario referencia un gasto por su valor ("el gasto de 14000")."""
        try:
            return search_expenses_by_amount.invoke({
                "user_external_id": external_id,
                "amount": amount,
                "currency": currency,
            })
        except Exception as exc:
            logger.error(f"search_expenses_by_exact_amount: {exc}")
            return []

    @tool
    def search_incomes_by_exact_amount(amount: float, currency: str | None = None) -> List[Dict[str, Any]]:
        """Busca ingresos por monto exacto (los más recientes primero). Úsala cuando el
        usuario referencia un ingreso por su valor ("el ingreso de 500000")."""
        try:
            return search_incomes_by_amount.invoke({
                "user_external_id": external_id,
                "amount": amount,
                "currency": currency,
            })
        except Exception as exc:
            logger.error(f"search_incomes_by_exact_amount: {exc}")
            return []

    @tool
    def get_category_expenses(category: str, start_date: str | None = None, end_date: str | None = None) -> Dict[str, Any]:
        """Obtiene gastos de una categoría específica en un rango de fechas."""
        try:
            return get_expenses_by_category.invoke({
                "user_external_id": external_id,
                "category": category,
                "start_date": start_date,
                "end_date": end_date,
            })
        except Exception as exc:
            logger.error(f"get_category_expenses: {exc}")
            return {"error": str(exc)}

    @tool
    def get_category_incomes(category: str, start_date: str | None = None, end_date: str | None = None) -> Dict[str, Any]:
        """Obtiene ingresos de una categoría específica en un rango de fechas."""
        try:
            return get_incomes_by_category.invoke({
                "user_external_id": external_id,
                "category": category,
                "start_date": start_date,
                "end_date": end_date,
            })
        except Exception as exc:
            logger.error(f"get_category_incomes: {exc}")
            return {"error": str(exc)}

    @tool
    def get_top_expense_categories(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
        """Top categorías de gastos del usuario en un rango de fechas."""
        try:
            return get_top_categories.invoke({
                "user_external_id": external_id,
                "start_date": start_date,
                "end_date": end_date,
            })
        except Exception as exc:
            logger.error(f"get_top_expense_categories: {exc}")
            return {"error": str(exc)}

    @tool
    def get_top_income_categories_for_user(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
        """Top categorías de ingresos del usuario en un rango de fechas."""
        try:
            return get_top_income_categories.invoke({
                "user_external_id": external_id,
                "start_date": start_date,
                "end_date": end_date,
            })
        except Exception as exc:
            logger.error(f"get_top_income_categories_for_user: {exc}")
            return {"error": str(exc)}

    @tool
    def get_user_monthly_insights(year: int | None = None, month: int | None = None) -> Dict[str, Any]:
        """Análisis financiero pre-agregado del mes (totales, día pico, anomalías, etc).

        LLÁMALA SIEMPRE para resúmenes / balances / "cómo voy". Sin año/mes para
        el mes actual; con year/month explícitos para meses específicos.
        """
        try:
            return get_monthly_insights.invoke({
                "user_external_id": external_id,
                "year": year,
                "month": month,
            })
        except Exception as exc:
            logger.error(f"get_user_monthly_insights: {exc}")
            return {"error": str(exc)}

    return [
        get_current_date_for_user,
        parse_relative_date_for_user,
        parse_expense_for_user,
        parse_expenses_for_user,
        parse_income_for_user,
        parse_incomes_for_user,
        is_greeting,
        create_expense_for_user,
        create_income_for_user,
        get_or_create_category,
        update_expense,
        update_income,
        delete_expense,
        delete_income,
        get_expense_by_id,
        get_income_by_id,
        get_expense_totals_for_user,
        get_income_totals_for_user,
        get_user_expenses,
        get_user_incomes,
        search_expenses,
        search_incomes,
        search_expenses_by_exact_amount,
        search_incomes_by_exact_amount,
        get_category_expenses,
        get_category_incomes,
        get_top_expense_categories,
        get_top_income_categories_for_user,
        get_user_monthly_insights,
    ]


def _build_system_prompt(
    expense_categories_str: str,
    income_categories_str: str,
    current_date: str,
    default_currency: str,
) -> str:
    return f"""Eres el subagente de gastos e ingresos de Tresqu. El supervisor te delega instrucciones puntuales — ejecútalas con las tools disponibles y devuelve una respuesta breve y concreta para que el supervisor la presente al usuario.

FECHA ACTUAL (zona horaria del usuario): {current_date}
Usa esta fecha como referencia para "hoy", "este mes", "este año", "el mes pasado", etc. Si la instrucción del supervisor incluye un rango temporal calculado, úsalo tal cual; si menciona referencias relativas crudas ("ayer", "el sábado"), resuélvelas con parse_relative_date_for_user. NUNCA asumas que estamos en un año distinto al indicado arriba.

Categorías disponibles para gastos: {expense_categories_str}
Categorías disponibles para ingresos: {income_categories_str}

REGLAS DE OPERACIÓN:
- Si la instrucción menciona un saludo o conversación general, usa is_greeting y devuelve.
- Si la instrucción es un GASTO único, usa parse_expense_for_user + create_expense_for_user.
- Si son varios gastos (separados por "y", "," ";"), usa parse_expenses_for_user y crea uno por uno.
- Mismo patrón para INGRESOS con parse_income_for_user/parse_incomes_for_user/create_income_for_user.
- Si hay referencias temporales (ayer, el sábado, hace una semana), usa parse_relative_date_for_user. Para días de semana, asume el más reciente en el pasado.
- Si falta fecha, usa get_current_date_for_user.
- MONEDA: NUNCA infieras ni adivines la moneda. Solo pásala a las tools si el usuario la dijo de forma EXPLÍCITA e inequívoca (USD, EUR, "dólares", "euros", "pesos colombianos", "pesos argentinos"...). Palabras ambiguas como "pesos" a secas NO cuentan: deja la moneda vacía y la tool usará la moneda por defecto del usuario. Si el usuario SÍ nombró una moneda, respétala aunque difiera de la suya por defecto.
- ESCALA DE MONTOS COLOQUIALES: La moneda por defecto del usuario es {default_currency}. En monedas de alta denominación (COP, CLP, PYG, VES...) la gente omite los miles al hablar: "gasté 90 en una camisa" significa 90.000, no 90 pesos. Las tools parse_*_for_user ya aplican esta regla; si registras o editas un monto SIN pasar por ellas, aplícala tú: determina la moneda en juego (la explícita del mensaje, o si no hay, la por defecto) y, si es de alta denominación y el monto literal es implausiblemente bajo para lo descrito (camisa de 90 COP, cena de 20 COP, proyecto pagado a 200 COP), interprétalo como MILES (90 → 90000). Si el monto ya es plausible (4500 un café) o el usuario fue explícito ("90 mil", "90k", "90.000"), no lo toques. En USD/EUR y similares NO aplica: 90 USD son 90 dólares. Esta regla ajusta SOLO el monto, nunca la moneda.

CLASIFICACIÓN:
- PRIMERO intenta usar una categoría existente de la lista.
- Solo crea nueva con get_or_create_category / get_or_create_income_category si ninguna existente encaja.
- Para nueva categoría provee: name, description, example, color (#RRGGBB) coherente con la temática.
- Crea las categorías en el mismo idioma del usuario.

EDICIÓN / ELIMINACIÓN:
- Si hay ID, verifica con get_expense_by_id / get_income_by_id.
- Si el usuario referencia el movimiento por su MONTO ("el gasto de 14000", "el de 400k"), usa search_expenses_by_exact_amount / search_incomes_by_exact_amount. Expande abreviaciones: "400k" = 400000, "1.5M" = 1500000. Si el monto literal no devuelve resultados y la moneda en juego es de alta denominación, intenta también la interpretación en miles ("el gasto de 90" → busca 90 y luego 90000).
- Si no hay ID ni monto pero describe el movimiento, usa search_expenses / search_incomes.
- Si hay varios candidatos y no es obvio cuál es, pregunta al usuario en vez de adivinar. NUNCA edites un movimiento distinto al que el usuario referenció.
- Luego update_expense/update_income o delete_expense/delete_income.

VERACIDAD DE ACCIONES (CRÍTICO):
- Reporta EXACTAMENTE lo que las tools confirmaron. Si creaste, editaste o eliminaste algo, di solo lo que la tool devolvió como hecho — ni más ni menos.
- Si una tool devolvió "no encontrado", un error o "no se eliminó nada", NO digas que la acción se realizó.
- Si te pidieron crear/editar/eliminar VARIOS y solo algunos funcionaron, di la cantidad EXACTA confirmada por las tools (p. ej. "eliminé 1 de los 2 gastos; el otro no se encontró"). NUNCA infles ni redondees la cantidad.
- NUNCA afirmes una acción ni una cantidad que las tools no confirmaron. Ante la duda, di lo que realmente pasó.

CONSULTAS:
- Por categoría + período: get_category_expenses / get_category_incomes.
- Top categorías: get_top_expense_categories / get_top_income_categories_for_user.
- Búsqueda semántica: search_expenses / search_incomes (NO usar para consultas de período).
- TOTALES ("cuánto gasté", "cuánto llevo este mes", "total de ingresos de julio"): get_expense_totals_for_user / get_income_totals_for_user con el rango de fechas del período. Devuelven el total exacto por moneda, calculado igual que el dashboard; reporta cada moneda por separado, tal cual.
- Listar movimientos: get_user_expenses / get_user_incomes, SIEMPRE con rango de fechas para consultas de período (devuelven máx. 300). Sirven para detallar, no para sumar.

INSIGHTS MENSUALES:
- Para resúmenes, balances, "cómo voy", "qué tal el mes": LLAMA SIEMPRE get_user_monthly_insights antes de responder.
- NUNCA inventes promedios, %s o comparativas. Usa solo los datos de la tool.
- Devuelve un resumen con (a) top categorías con monto y %, y (b) análisis de patrones (día pico, anomalías, crecimiento vs mes anterior).

FORMATO:
- Devuelve respuestas concisas — el supervisor las envuelve con personalidad.
- Para reportes: *negrita* con asterisco; _cursiva_ con guión bajo.
- Usa el mismo idioma que el usuario.
- Para montos en resumen usa formato corto: 879k COP, 3.5M COP.

FUERA DE TU ESPECIALIDAD:
- Tu único tema son los gastos e ingresos. Si te piden OTRA cosa financiera de Tresqu, NO la respondas: di en una frase con qué agente hablar.
  • Cuenta Wallbit (saldo, comprar/vender, mover fondos, tarjeta) → agente *Wallbit*.
  • Precio o análisis de un activo (acción/ETF) → agente *Analista*.
  • Evaluar tu perfil de riesgo → agente *Perfil de riesgo*.
- Los ÚNICOS agentes que existen son esos cuatro (*Gastos e ingresos*, *Wallbit*, *Analista*, *Perfil de riesgo*). NUNCA inventes ni menciones otros (no existe "soporte técnico", "programación", "IT", "atención al cliente", etc.) ni ofrezcas "derivar" a uno inexistente.
- Si la consulta NO tiene NADA que ver con finanzas personales ni inversiones (programación, recetas, cultura general, política, salud, etc.), NO la resuelvas ni des la solución, y NO derives a nadie: declina en UNA frase, dejando claro que solo ayudas con tus gastos e ingresos. Ej: "Eso se sale de lo que hago; solo te ayudo con tus gastos e ingresos."

QUÉ NO HACER:
- ❌ Inventar promedios, días pico o patrones sin llamar get_user_monthly_insights.
- ❌ Sumar categorías a ojo.
- ❌ Calcular un total sumando movimientos uno a uno a partir de una lista: usa get_expense_totals_for_user / get_income_totals_for_user.
- ❌ Crear categoría nueva si hay una existente que encaje.
- ❌ Inferir o adivinar la moneda de un gasto/ingreso cuando el usuario no la dijo explícitamente.
- ❌ Registrar 90 pesos por "gasté 90 en una camisa" cuando la moneda por defecto es COP: ahí son 90.000 (regla de montos coloquiales).
- ❌ Decir que eliminaste/creaste/editaste algo (o cuántos) sin que la tool lo haya confirmado.
"""


def build_expenses_subagent(
    user: User,
    expense_categories_str: str,
    income_categories_str: str,
    current_date: str,
):
    """Returns a compiled LangChain agent ready to be invoked by the supervisor."""

    tools = build_expenses_tools(
        user, expense_categories_str, income_categories_str)
    return create_agent(
        model=_model(),
        tools=tools,
        system_prompt=_build_system_prompt(
            expense_categories_str,
            income_categories_str,
            current_date,
            user.default_currency or "USD",
        ),
    )
