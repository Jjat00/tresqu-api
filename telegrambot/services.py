from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain.output_parsers import StructuredOutputParser, ResponseSchema
from langchain_core.tools import Tool, tool
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain.agents import create_openai_tools_agent
from langchain.agents import AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from datetime import datetime
from django.conf import settings
from users.models import User

llm = ChatOpenAI(model="gpt-4o", temperature=0.3,
                 api_key=settings.OPENAI_API_KEY)

embeddings = OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY,
                              model="text-embedding-3-small")

# Definir modelo de datos usando Pydantic


class ExpenseData(BaseModel):
    """Información sobre un gasto financiero."""

    amount: float = Field(description="Cantidad numérica del gasto")
    currency: str = Field(
        description="Código ISO de moneda (COP, MXN, USD, etc.)")
    category: str = Field(description="Categoría (Comida, Transporte, etc.)")
    spent_at: str = Field(description="Fecha del gasto en formato YYYY-MM-DD")
    note: str = Field(description="Descripción del gasto")

# Definir herramientas


@tool
def get_current_date() -> str:
    """Obtiene la fecha actual en formato YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


@tool
def parse_expense(text: str) -> dict:
    """
    Analiza un mensaje de texto para extraer información sobre un gasto.
    El texto debe describir un gasto como: "comida restaurante 50000 cop"
    """
    # Procesamos con LLM
    structured_llm = llm.with_structured_output(ExpenseData)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """
        Eres un asistente financiero experto en extraer información de gastos.
        Si el texto menciona un gasto, extrae la información solicitada.
        Si no hay información suficiente, haz tu mejor estimación.
        Si el texto no menciona ningún gasto (como saludos), genera un error.
        """),
        ("human", "{text}")
    ])

    try:
        chain = prompt | structured_llm
        result = chain.invoke({"text": text})
        return dict(result)
    except Exception as e:
        return {"error": str(e)}


@tool
def is_greeting(text: str) -> bool:
    """
    Determina si un mensaje es un saludo simple.
    """
    common_greetings = ["hola", "hello", "hey", "hi",
                        "buenos días", "buenas tardes", "buenas noches"]
    return text.lower().strip() in common_greetings or len(text.strip()) < 10


# Crear agente con las herramientas
tools = [get_current_date, parse_expense, is_greeting]

agent_prompt = ChatPromptTemplate.from_messages([
    ("system", """Eres un asistente financiero diseñado para analizar gastos.
    
    COMPORTAMIENTO:
    1. Si el mensaje es un saludo o muy corto (menos de 10 caracteres), sólo responde al saludo.
    2. Si el mensaje parece un gasto (ej: "comida restaurante 10000 cop"), extrae la información del gasto.
    3. Usa la herramienta get_current_date para obtener la fecha actual si no se especifica una.
    
    Debes responder con los datos en español.
    """),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

agent = create_openai_tools_agent(llm, tools, agent_prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)


def process_message(user: User, raw_text: str) -> dict:
    """
    Procesa un mensaje de texto para extraer información financiera
    y generar embeddings para búsqueda vectorial
    """
    try:
        # Ejecutar el agente con el texto del usuario
        result = agent_executor.invoke({"input": raw_text})
        output = result.get("output", "")

        # Generar embedding del texto - debe ser array de 1536 dimensiones
        text_embedding = embeddings.embed_query(raw_text)

        # Verificar si es un saludo usando la herramienta directamente
        if is_greeting(raw_text):
            return {
                "parsed_data": {
                    "amount": 0,
                    "currency": "",
                    "category": "Conversación",
                    "spent_at": "",
                    "note": raw_text
                },
                "embedding": text_embedding,
                "is_greeting": True
            }

        # Intentar extraer datos del gasto usando el resultado del agente o directamente
        try:
            expense_data = parse_expense(raw_text)

            # Si hay error o no se encontró un gasto, manejar como conversación
            if "error" in expense_data:
                return {
                    "parsed_data": {
                        "amount": 0,
                        "currency": "",
                        "category": "Conversación",
                        "spent_at": get_current_date(),
                        "note": raw_text
                    },
                    "embedding": text_embedding,
                    "is_conversation": True
                }

            # Asegurarse de que haya fecha, de lo contrario usar fecha actual
            if not expense_data.get("spent_at"):
                expense_data["spent_at"] = get_current_date()

            # Retornar datos estructurados del gasto
            return {
                "parsed_data": expense_data,
                "embedding": text_embedding
            }
        except Exception as parsing_error:
            # Si hay error al extraer datos, tratar como conversación
            print(f"Error al parsear gasto: {parsing_error}")
            return {
                "parsed_data": {
                    "amount": 0,
                    "currency": "",
                    "category": "Conversación",
                    "spent_at": get_current_date(),
                    "note": raw_text
                },
                "embedding": text_embedding,
                "is_conversation": True
            }
    except Exception as e:
        # Manejar errores generales
        print(f"Error al procesar mensaje: {e}")
        return {
            "error": str(e)
        }


def generate_response(user: User, parsed_data: dict) -> str:
    """
    Genera una respuesta amigable basada en los datos analizados
    """
    if "error" in parsed_data:
        return "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"

    # Si es un saludo, responder de manera conversacional
    if parsed_data.get("is_greeting", False) or parsed_data.get("is_conversation", False):
        user_name = user.first_name if user.first_name else "amigo"
        if parsed_data.get("is_greeting", False):
            return f"¡Hola {user_name}! 👋 Soy tu asistente de finanzas personales. Puedes contarme tus gastos y los registraré automáticamente. ¿En qué puedo ayudarte hoy?"
        else:
            return f"Entiendo, {user_name}. ¿Hay algún gasto que quieras registrar o alguna otra forma en que pueda ayudarte con tus finanzas personales?"

    data = parsed_data["parsed_data"]

    # Formatear respuesta
    response = f"✅ He registrado tu gasto:\n"
    response += f"📊 Categoría: {data.get('category', 'No especificada')}\n"
    response += f"💰 Monto: {data.get('amount', '?')}{data.get('currency', '')}\n"
    response += f"📅 Fecha: {data.get('spent_at', 'Hoy')}\n"

    if data.get('note'):
        response += f"📝 Nota: {data.get('note')}\n"

    response += "\n¿Hay algo más en lo que pueda ayudarte?"

    return response
