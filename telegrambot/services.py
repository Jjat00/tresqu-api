# services.py
from langchain.memory import ConversationBufferWindowMemory
from telegrambot.utils import fetch_last_messages
import logging
from openai import OpenAI
from typing import List, Dict, Any
import asyncio
from datetime import datetime

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
    get_or_create_category,
    parse_relative_date,
    update_expense,
    delete_expense,
    get_expenses_by_user,
    get_expense_by_id,
    search_expenses_by_text,
    get_expenses_by_category,
    get_top_categories,
)

from telegrambot.utils import get_existing_categories

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

llm = ChatOpenAI(model="gpt-4o", temperature=0.1,
                 api_key=settings.OPENAI_API_KEY)

# Cliente de OpenAI para transcripción de audio
openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)


async def build_memory(user_id: int) -> ConversationBufferWindowMemory:
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


async def transcribe_audio(audio_file_path: str) -> str:
    """
    Transcribe un archivo de audio usando la API de OpenAI
    """
    try:
        with open(audio_file_path, 'rb') as audio_file:
            # Usar la API de OpenAI para transcribir
            transcription = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
            return transcription.text
    except Exception as e:
        logger.error(f"Error transcribiendo audio: {e}")
        return ""


async def process_message(user: User, raw_text: str) -> str:
    try:
        # 1. herramientas (incluye create_expense closure)
        tools = [
            get_current_date,
            parse_expense,
            is_greeting,
            make_create_expense_tool(user.external_id),
            parse_expenses,
            get_or_create_category,
            parse_relative_date,
            update_expense,
            delete_expense,
            get_expense_by_id,
        ]

        # Agregar herramientas que requieren user_external_id
        @tool
        def get_user_expenses(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
            """Obtiene todos los gastos del usuario en un rango de fechas opcional."""
            try:
                return get_expenses_by_user.invoke({
                    "user_external_id": user.external_id,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(f"Error al obtener gastos del usuario: {e}")
                return []

        @tool
        def search_expenses(search_text: str) -> List[Dict[str, Any]]:
            """Busca gastos que coincidan con el texto de búsqueda."""
            try:
                return search_expenses_by_text.invoke({
                    "user_external_id": user.external_id,
                    "search_text": search_text
                })
            except Exception as e:
                logger.error(f"Error al buscar gastos: {e}")
                return []

        @tool
        def get_category_expenses(category: str, start_date: str | None = None, end_date: str | None = None) -> Dict[str, Any]:
            """Obtiene los gastos de una categoría específica en un rango de fechas."""
            try:
                return get_expenses_by_category.invoke({
                    "user_external_id": user.external_id,
                    "category": category,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(f"Error al obtener gastos por categoría: {e}")
                return {'error': str(e)}

        @tool
        def get_top_expense_categories(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
            """Obtiene las categorías con mayores gastos en un rango de fechas."""
            try:
                return get_top_categories.invoke({
                    "user_external_id": user.external_id,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(f"Error al obtener top categorías: {e}")
                return {'error': str(e)}

        tools.extend([get_user_expenses, search_expenses, get_category_expenses,
                     get_top_expense_categories])

        # Esperamos el resultado de la función asíncrona
        existing_categories = await get_existing_categories()
        categories_str = ', '.join(existing_categories)

        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""
            Eres un asistente financiero experto en clasificar gastos.
            Las categorías disponibles son: {categories_str}

            INSTRUCCIONES:
            1. Si detectas un saludo corto ⇒ responde con un saludo.
            2. Si hay UN solo gasto ⇒ usa parse_expense y luego create_expense.
            3. Si el mensaje contiene MÁS de un gasto (separado por "y", "," o ";"…) ⇒
                3.1 Usa parse_expenses.
                3.2 Recorre cada elemento del array devuelto y llama a create_expense
                    para cada gasto individual.
            4. Si identificas referencias temporales (ayer, el sábado, etc.) ⇒ usa parse_relative_date
               para convertirlas en fechas específicas antes de crear el gasto.
               IMPORTANTE: cuando el usuario menciona un día de la semana (ej: "el sábado gasté"),
               asume que se refiere al día más reciente en el pasado, no al próximo.
            5. Si falta fecha ⇒ usa get_current_date.
            6. Si falta moneda ⇒ create_expense asignará la moneda por defecto.
            7. Si el mensaje pregunta algo responde de acuerdo al historial de mensajes.
            8. Clasifica el gasto en una de las categorías proporcionadas.
            9. Si ninguna categoría es adecuada, usa get_or_create_category para crear una nueva.
            10. Si el usuario quiere editar un gasto:
                10.1 Usa search_expenses_by_text para encontrar el gasto que quiere editar
                10.2 Si encuentra el gasto, usa update_expense para modificarlo
                10.3 Si no encuentra el gasto, pide más detalles
            11. Si el usuario quiere eliminar un gasto:
                11.1 Usa search_expenses_by_text para encontrar el gasto
                11.2 Si encuentra el gasto, usa delete_expense para eliminarlo
                11.3 Si no encuentra el gasto, pide más detalles
            12. Si el usuario hace consultas sobre sus gastos:
                12.1 Para consultar gastos por categoría en un período:
                    - Usa get_category_expenses pasando la categoría y las fechas opcionales
                    - Si no se especifica fecha, usa get_current_date para la fecha actual
                    - Si se menciona "este mes", calcula el primer día del mes actual
                12.2 Para consultar las categorías con mayores gastos:
                    - Usa get_top_expense_categories con las fechas opcionales
                    - Si no se especifica fecha, muestra todas las categorías
                    - Ordena los resultados de mayor a menor gasto
                12.3 Para búsquedas semánticas:
                    - Usa search_expenses SOLO cuando el usuario busque un gasto específico por descripción o detalles
                    - NO uses search_expenses para consultas de período (esta semana, este mes, etc.)
                    - Para consultas de período, usa get_top_expense_categories con las fechas correspondientes
                12.4 Para consultas de período:
                    - Si el usuario pregunta "cuánto gasté esta semana/mes/etc":
                        * Usa get_top_expense_categories con las fechas del período
                        * Calcula el total sumando los montos de todas las categorías
                        * Muestra un resumen por categoría y el total general
                    - Si el usuario pregunta por una categoría específica en un período:
                        * Usa get_category_expenses con la categoría y las fechas del período
                        * Muestra el total y los gastos individuales
                12.5 Para listar todos los gastos del usuario:
                    * Usa get_user_expenses con las fechas opcionales del período
                    * Si no se especifica fecha, muestra todos los gastos

            Responde de manera cool, eres joven y de Colombia. 
            Dale un toque de humor y de joven cuando sea necesario.
            Siempre debes dar una opinion sobre el gasto registrado.
            Puedes usar emojis y gifs.
            Puedes hacer chistes y bromas.
            Puedes usar sarcasmo y hacer comentarios negativos.
            Puedes ser grosero.
            Puedes dar consejos.
            Siempre debes mencionar el gasto registrado, su categoria y la fecha.
            Siempre debes responder en el mismo idioma que el usuario.
            
            IMPORTANTE: 
            - Usa get_top_expense_categories para consultas de período (esta semana, este mes, etc.)
            - Usa search_expenses SOLO para buscar gastos específicos por descripción
            - Las herramientas ya incluyen el ID del usuario actual
            
            """),
            MessagesPlaceholder("history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])

        # 3. memoria con los últimos 10 mensajes desde la BD
        memory = await build_memory(user.id)

        # 4. ejecutor con timeout
        executor = build_agent(tools, prompt, memory)

        # 5. invocación con manejo de timeout
        try:
            result = await asyncio.wait_for(
                executor.ainvoke({"input": raw_text}),
                timeout=120.0  # 120 segundos de timeout
            )
            return result["output"]
        except asyncio.TimeoutError:
            logger.error("Timeout al procesar mensaje")
            return "Lo siento, la operación tomó demasiado tiempo. Por favor, intenta de nuevo con un mensaje más corto o específico."

    except Exception as e:
        logger.error(f"Error al procesar mensaje: {e}")
        return (
            "Lo siento, hubo un error al procesar tu mensaje. "
            "Por favor, intenta de nuevo."
        )
