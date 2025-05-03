from datetime import datetime
import logging
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from django.conf import settings
from django.utils import timezone
from langchain_openai import OpenAIEmbeddings
from pydantic import BaseModel
from typing import List, Dict, Any
# models
from expenses.models import Expense
from users.models import User
# serializasers
from .serializasers import ExpenseData
from .currencies import is_valid_currency
# Import embeddings service

embeddings = OpenAIEmbeddings(
    api_key=settings.OPENAI_API_KEY,
    model="text-embedding-3-small",
)


llm = ChatOpenAI(model="gpt-4o", temperature=0.3,
                 api_key=settings.OPENAI_API_KEY)

# Configuración de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class ExpenseList(BaseModel):
    """Lista de gastos detectados en un solo mensaje."""
    expenses: List[ExpenseData]


@tool
def get_current_date() -> str:
    """Devuelve la fecha actual en formato YYYY‑MM‑DD."""
    return datetime.now().strftime("%Y-%m-%d")


@tool()
async def parse_expense(text: str) -> dict:
    """
    Analiza un mensaje para extraer un gasto.
    Devuelve dict o {'error': …} si no hay datos suficientes.
    """
    structured_llm = llm.with_structured_output(
        ExpenseData, method="function_calling")

    prompt = ChatPromptTemplate.from_messages([
        ("system", """
        Eres un asistente financiero experto en extraer información de gastos.
        Extrae un gasto y devuélvelo como ExpenseData.
        Si el texto menciona un gasto, extrae la información solicitada.
        Si no hay información suficiente, haz tu mejor estimación.
        Si el texto no menciona ningún gasto (como saludos), genera un error.
        """),
        ("human", "{text}")
    ])

    try:
        chain = prompt | structured_llm
        result = await chain.ainvoke({"text": text})
        return dict(result)
    except Exception as e:
        return {"error": str(e)}


@tool
async def parse_expenses(text: str) -> Dict[str, Any]:
    """Extrae VARIOS gastos."""
    chain = (ChatPromptTemplate.from_messages([
        ("system", """
        Eres un asistente financiero experto en extraer información de gastos.
        Extrae todos los gastos y devuélvelos como una lista de ExpenseData.
        Si el texto menciona gastos, extrae la información solicitada.
        Si no hay información suficiente, haz tu mejor estimación.
        Si el texto no menciona ningún gasto (como saludos), genera un error.
        """),
        ("human", "{text}")
    ]) | llm.with_structured_output(
        ExpenseList, method="function_calling"))
    try:
        result: ExpenseList = await chain.ainvoke({"text": text})
        return result.model_dump()
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


@tool
def create_expense(
    user_external_id: str,
    amount: float,
    currency: str,
    category: str,
    spent_at: str | None = None,
    note: str | None = ""
) -> str:
    """Crea un gasto en la base de datos y devuelve un resumen confirmatorio."""
    # lookup del usuario sin importar dónde estés llamando
    user = User.objects.get(external_id=user_external_id)

    # Verificar si la moneda es válida, de lo contrario usar la moneda por defecto del usuario
    if not currency or not is_valid_currency(currency):
        currency = user.default_currency
        currency_message = f"(Se usó tu moneda por defecto: {currency})"
    else:
        currency_message = ""

    if spent_at:
        date = datetime.strptime(spent_at, "%Y-%m-%d").date()
    else:
        date = timezone.now().date()

    # Crear el texto para el embedding
    expense_text = f"Gasto de {amount} {currency} en {category} el {date}. {note}"

    # Generar embedding
    embedding = None
    try:
        embedding = embeddings.embed_query(expense_text)
    except Exception as e:
        logger.error(f"Error al generar embedding para gasto: {e}")

    expense = Expense.objects.create(
        user=user, amount=amount, currency=currency,
        category_str=category, spent_at=date, note=note,
        timestamp=timezone.now(), embedding=embedding,
        raw_message=expense_text
    )
    return (
        f"✅ ¡Gasto registrado!\n"
        f"📊 Categoría: {category}\n"
        f"💰 Monto: {amount} {currency} {currency_message}\n"
        f"📅 Fecha: {date}\n"
        f"{'📝 Nota: ' + note if note else ''}"
    )
