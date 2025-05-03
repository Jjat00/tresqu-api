from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain.output_parsers import StructuredOutputParser, ResponseSchema
from django.conf import settings
from users.models import User

llm = ChatOpenAI(model="gpt-4o", temperature=0.3,
                 api_key=settings.OPENAI_API_KEY)

embeddings = OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY,
                              model="text-embedding-3-small")

# Definir esquema para analizar gastos
expense_parser = StructuredOutputParser.from_response_schemas([
    ResponseSchema(name="amount", description="Cantidad numérica del gasto"),
    ResponseSchema(name="currency",
                   description="Código ISO de moneda (MXN, USD, etc.)"),
    ResponseSchema(name="category",
                   description="Categoría (Comida, Transporte, etc.)"),
    ResponseSchema(name="spent_at",
                   description="Fecha del gasto en formato YYYY-MM-DD"),
    ResponseSchema(name="note", description="Descripción del gasto"),
])


def process_message(user: User, raw_text: str) -> dict:
    """
    Procesa un mensaje de texto para extraer información financiera
    y generar embeddings para búsqueda vectorial
    """
    # Primero verificamos si el mensaje parece ser un gasto
    common_greetings = ["hola", "hello", "hey", "hi",
                        "buenos días", "buenas tardes", "buenas noches"]
    if raw_text.lower().strip() in common_greetings or len(raw_text.strip()) < 10:
        # Generar embedding para el mensaje
        text_embedding = embeddings.embed_query(raw_text)

        # Devolver respuesta sin procesar como gasto
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

    prompt = f"""
    Analiza el siguiente mensaje y extrae información sobre un gasto:
    
    {raw_text}
    
    {expense_parser.get_format_instructions()}
    """

    try:
        # Procesar el mensaje con LLM
        response = llm.invoke(prompt)
        parsed_data = expense_parser.parse(response.content)

        # Generar embedding del texto - debe ser array de 1536 dimensiones
        text_embedding = embeddings.embed_query(raw_text)

        # Retornar resultado y embedding
        return {
            "parsed_data": parsed_data,
            "embedding": text_embedding
        }
    except Exception as e:
        # Manejar errores
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
    if parsed_data.get("is_greeting", False):
        user_name = user.first_name if user.first_name else "amigo"
        return f"¡Hola {user_name}! 👋 Soy tu asistente de finanzas personales. Puedes contarme tus gastos y los registraré automáticamente. ¿En qué puedo ayudarte hoy?"

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
