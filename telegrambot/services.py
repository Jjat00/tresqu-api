# services.py
from langchain.memory import ConversationBufferWindowMemory
from telegrambot.utils import fetch_last_messages
from langchain_core.runnables.history import RunnableWithMessageHistory
import logging
from typing import List

from django.conf import settings
from users.models import User

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.tools import tool

from telegrambot.tools import (
    get_current_date,
    parse_expense,
    is_greeting,
    create_expense,
    parse_expenses,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

llm = ChatOpenAI(model="gpt-4o", temperature=0.3,
                 api_key=settings.OPENAI_API_KEY)


async def build_memory(user_id: int) -> ConversationBufferWindowMemory:
    mem = ConversationBufferWindowMemory(
        k=10, memory_key="history", return_messages=True
    )
    print("👀👀", user_id)
    async for msg in fetch_last_messages(user_id):
        print("😎😎", msg)
        mem.chat_memory.add_message(msg)
    return mem


def build_agent(tools, prompt, memory) -> AgentExecutor:
    agent = create_openai_tools_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,                  # 👈 aquí va el búfer pre‑cargado
        return_intermediate_steps=True,
        verbose=True,
    )


def make_create_expense_tool(user_external_id: str):
    """
    Devuelve un StructuredTool que NO expone user_external_id;
    el ID vive en el cierre (closure).
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


def build_agent_for_user(user_external_id: str) -> RunnableWithMessageHistory:

    tool_expense = make_create_expense_tool(user_external_id)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """
        Eres un asistente financiero.

        INSTRUCCIONES:
        1. Si detectas un saludo corto ⇒ responde con un saludo.
        2. Si hay UN solo gasto ⇒ usa parse_expense y luego create_expense.
        3. Si el mensaje contiene MÁS de un gasto (separado por “y”, “,”, “;”…) ⇒
            3.1 Usa parse_expenses.
            3.2 Recorre cada elemento del array devuelto y llama a create_expense
                para cada gasto individual.
        4. Si falta fecha ⇒ usa get_current_date.
        5. Si falta moneda ⇒ create_expense asignará la moneda por defecto.
        6. Si el mensaje pregunta algo responde de acuerdo al historial de mensajes.
        Responde SIEMPRE en español.
        """),
        MessagesPlaceholder("history"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    tools: List = [
        get_current_date,
        parse_expense,
        is_greeting,
        tool_expense,
        parse_expenses,
    ]

    agent = create_openai_tools_agent(llm, tools, prompt)

    core_executor = AgentExecutor(
        agent=agent,
        tools=tools,
        return_intermediate_steps=True,
        verbose=True,
    )

    executor = RunnableWithMessageHistory(
        core_executor,
        input_messages_key="input",
        history_messages_key="history",
    ).with_config({"run_name": f"cashbot-{user_external_id}"})

    return executor


async def process_message(user: User, raw_text: str) -> str:
    try:
        # 1. herramientas (incluye create_expense closure)
        tools = [
            get_current_date,
            parse_expense,
            is_greeting,
            make_create_expense_tool(user.external_id),
            parse_expenses,
        ]

        # 2. prompt con placeholder de history
        prompt = ChatPromptTemplate.from_messages([
            ("system", "...tus reglas..."),
            MessagesPlaceholder("history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])

        # 3. memoria con los últimos 10 mensajes desde la BD
        memory = await build_memory(user.id)

        # 4. ejecutor
        executor = build_agent(tools, prompt, memory)

        # 5. invocación
        result = await executor.ainvoke({"input": raw_text})
        return result["output"]

    except Exception as e:
        logger.error(f"Error al procesar mensaje: {e}")
        return (
            "Lo siento, hubo un error al procesar tu mensaje. "
            "Por favor, intenta de nuevo."
        )
