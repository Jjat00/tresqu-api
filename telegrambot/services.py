# services.py
from langchain.memory import ConversationBufferWindowMemory
from telegrambot.utils import fetch_last_messages
import logging
from openai import OpenAI

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
        k=10, memory_key="history", return_messages=True
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


async def process_voice_message(user: User, voice_file_path: str) -> str:
    """
    Procesa un mensaje de voz de Telegram, lo transcribe y extrae información de gastos
    """
    try:
        # Transcribir el audio
        transcription = await transcribe_audio(voice_file_path)

        if not transcription:
            return "Lo siento, no pude entender el audio. Por favor, intenta de nuevo con un mensaje de texto o un audio más claro."

        # Procesar el texto transcrito
        return await process_message(user, transcription)

    except Exception as e:
        logger.error(f"Error procesando mensaje de voz: {e}")
        return "Lo siento, hubo un error al procesar tu mensaje de voz. Por favor, intenta de nuevo."


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
        ]

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
            
            """),
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
