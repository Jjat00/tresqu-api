# services.py
import logging
from openai import OpenAI
from typing import List, Dict, Any
import asyncio
from datetime import datetime
from langchain.memory import ConversationBufferWindowMemory
from whatsappbot.utils import fetch_last_messages

from django.conf import settings
from users.models import User

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.tools import tool

# Importamos las herramientas de telegrambot ya que son genéricas
# para nuestro caso de uso
from telegrambot.tools import (
    get_current_date,
    parse_expense,
    is_greeting,
    create_expense,
    parse_expenses,
    get_or_create_category,
    parse_relative_date,
    update_expense,
    delete_expense,
    get_expenses_by_user,
    get_expense_by_id,
    search_expenses_by_text,
    get_expenses_by_category,
    get_top_categories,
    # Herramientas de ingresos
    parse_income,
    parse_incomes,
    create_income,
    update_income,
    delete_income,
    get_incomes_by_user,
    get_income_by_id,
    search_incomes_by_text,
    get_incomes_by_category,
    get_top_income_categories,
    get_or_create_income_category,
)

from whatsappbot.utils import get_existing_categories, get_categories_with_details, get_existing_income_categories

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

llm = ChatOpenAI(model="gpt-4.1", temperature=0.1,
                 api_key=settings.OPENAI_API_KEY)

# Cliente de OpenAI para posibles integraciones futuras
openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)


async def build_memory(user_id: int) -> ConversationBufferWindowMemory:
    """Construye la memoria de conversación para el usuario"""
    mem = ConversationBufferWindowMemory(
        k=10,
        memory_key="history",
        return_messages=True,
        output_key="output"
    )
    async for msg in fetch_last_messages(user_id):
        mem.chat_memory.add_message(msg)
    return mem


def build_agent(tools, prompt, memory) -> AgentExecutor:
    """Crea un agente con las herramientas, prompt y memoria dadas"""
    agent = create_openai_tools_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        return_intermediate_steps=True,
        verbose=True,
    )


def make_create_expense_tool(user_external_id: str):
    """
    Crea una herramienta para registrar gastos específica para un usuario
    """
    @tool
    def create_expense_for_user(
        amount: float,
        currency: str,
        category: str,
        spent_at: str | None = None,
        note: str | None = "",
    ) -> str:
        """Registra un gasto en la base de datos y confirma el registro."""
        return create_expense.invoke(
            {
                "user_external_id": user_external_id,
                "amount": amount,
                "currency": currency,
                "category": category,
                "spent_at": spent_at,
                "note": note,
            }
        )

    return create_expense_for_user


def make_create_income_tool(user_external_id: str):
    """
    Crea una herramienta para registrar ingresos específica para un usuario
    """
    @tool
    def create_income_for_user(
        amount: float,
        currency: str,
        category: str,
        received_at: str | None = None,
        note: str | None = "",
        category_description: str | None = None,
        category_example: str | None = None,
        category_color: str | None = None
    ) -> str:
        """Registra un ingreso en la base de datos y confirma el registro."""
        # Primero crear la categoría de ingreso si no existe
        get_or_create_income_category.invoke({
            "name": category,
            "description": category_description,
            "example": category_example,
            "color": category_color
        })

        # Luego registrar el ingreso
        return create_income.invoke(
            {
                "user_external_id": user_external_id,
                "amount": amount,
                "currency": currency,
                "category": category,
                "received_at": received_at,
                "note": note,
            }
        )

    return create_income_for_user


async def process_message(user: User, raw_text: str) -> str:
    """
    Procesa un mensaje de WhatsApp y devuelve la respuesta del agente
    """
    try:
        # 1. herramientas (incluye create_expense closure)
        @tool
        def get_current_date_for_user() -> str:
            """Obtiene la fecha actual en el formato YYYY-MM-DD, considerando la zona horaria del usuario."""
            try:
                return get_current_date.invoke({
                    "user_external_id": user.external_id
                })
            except Exception as e:
                logger.error(f"Error al obtener la fecha actual: {e}")
                return datetime.now().strftime("%Y-%m-%d")

        @tool
        def parse_relative_date_for_user(date_text: str) -> str:
            """Convierte referencias temporales relativas a fechas específicas."""
            try:
                return parse_relative_date.invoke({
                    "date_text": date_text,
                    "user_external_id": user.external_id
                })
            except Exception as e:
                logger.error(f"Error al analizar fecha relativa: {e}")
                return datetime.now().strftime("%Y-%m-%d")

        tools = [
            get_current_date_for_user,
            parse_expense,
            parse_income,
            is_greeting,
            parse_relative_date_for_user,
            parse_expenses,
            parse_incomes,
            make_create_expense_tool(user.external_id),
            make_create_income_tool(user.external_id),
            get_or_create_category,
        ]

        # 2. configuración del prompt
        # Obtener fecha actual utilizando asyncio.to_thread para ejecutar código sincrónico
        current_date = await asyncio.to_thread(
            lambda: datetime.now().strftime("%Y-%m-%d")
        )

        system_message = f"""
        Eres un asistente financiero que ayuda a las personas a registrar sus gastos e ingresos. 
        Tu nombre es Cashbot y tu función es ayudar al usuario a registrar transacciones económicas.
        
        El usuario {user.first_name or 'sin nombre'} tiene configurada la moneda {user.default_currency or 'USD'}.
        
        - Si el usuario quiere registrar un gasto, primero debes usar parse_expense para analizarlo
        - Si el usuario quiere registrar un ingreso, primero debes usar parse_income para analizarlo
        - No hay categorías predefinidas, puedes crear nuevas categorías si es necesario
        
        Cuando te pidan algo que no puedes hacer, indícalo amablemente.
        
        Hoy es {current_date}
        """

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_message),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        # 3. memoria
        memory = await build_memory(user.id)

        # 4. construir agente
        agent = build_agent(tools, prompt, memory)

        # 5. ejecutar agente
        result = await asyncio.to_thread(
            agent.invoke,
            {"input": raw_text, "history": memory.chat_memory.messages},
        )

        # 6. devolver la respuesta
        return result["output"]

    except Exception as e:
        logger.exception(f"Error al procesar mensaje: {e}")
        return "Lo siento, hubo un error al procesar tu mensaje. ¿Puedes intentarlo de nuevo?"
