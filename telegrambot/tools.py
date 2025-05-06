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
from categories.models import Category
# serializasers
from .serializasers import ExpenseData
from .currencies import is_valid_currency
# Import embeddings service
import calendar
from datetime import datetime, timedelta

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


@tool
def parse_relative_date(date_text: str) -> str:
    """
    Convierte una referencia temporal relativa (ayer, el sábado, etc.) en una fecha específica.
    Toma como referencia la fecha actual.
    Devuelve la fecha en formato YYYY-MM-DD.
    """
    today = datetime.now()
    date_text = date_text.lower().strip()

    # Referencias básicas
    if "hoy" in date_text:
        return today.strftime("%Y-%m-%d")

    if "ayer" in date_text:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")

    if "anteayer" in date_text or "antes de ayer" in date_text:
        return (today - timedelta(days=2)).strftime("%Y-%m-%d")

    if "mañana" in date_text:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # Días de la semana
    days_spanish = {
        "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
        "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6
    }

    for day_name, day_num in days_spanish.items():
        if day_name in date_text:
            current_weekday = today.weekday()

            # Palabras que indican pasado
            past_indicators = ["gasté", "gaste", "compré", "compre", "pagué", "pague",
                               "fue", "fui", "hice", "pasado", "anterior", "última", "ultima"]

            # Revisar si hay indicadores de tiempo pasado en el texto
            is_past_reference = any(
                indicator in date_text for indicator in past_indicators)

            # Si es una referencia al pasado o no hay indicadores claros de futuro,
            # asumimos que se refiere al día más reciente en el pasado
            if is_past_reference or not any(future_word in date_text for future_word in ["próximo", "proximo", "siguiente", "que viene"]):
                # Si el día mencionado es posterior al actual en la semana actual
                if day_num > current_weekday:
                    # Vamos a la semana anterior
                    days_diff = day_num - current_weekday - 7
                else:
                    # Día en la semana actual pero anterior o igual al día actual
                    days_diff = day_num - current_weekday

                target_date = today + timedelta(days=days_diff)
            else:
                # Si hay un indicador claro de futuro
                if day_num >= current_weekday:
                    days_diff = day_num - current_weekday
                else:
                    days_diff = 7 + day_num - current_weekday

                target_date = today + timedelta(days=days_diff)

            # Si el texto contiene indicadores adicionales de tiempo
            if "pasado" in date_text or "anterior" in date_text or "última" in date_text or "ultima" in date_text:
                target_date = target_date - timedelta(days=7)

            if "próximo" in date_text or "proximo" in date_text or "siguiente" in date_text or "que viene" in date_text:
                target_date = target_date + timedelta(days=7)

            return target_date.strftime("%Y-%m-%d")

    # Si no se pudo interpretar, devolvemos la fecha actual
    return today.strftime("%Y-%m-%d")


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
        
        Si el mensaje incluye referencias temporales como "ayer", "el martes", 
        "la semana pasada", etc., debes identificarlas correctamente para establecer
        la fecha del gasto. Usa la fecha actual como referencia.
        
        Por ejemplo:
        - "ayer compré un regalo a 20K" debe registrarse con la fecha de ayer
        - "el sábado gasté 100k en cervezas" debe registrarse con la fecha del sábado más reciente
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
        
        Si el mensaje incluye referencias temporales como "ayer", "el martes", 
        "la semana pasada", etc., debes identificarlas correctamente para establecer
        la fecha del gasto. Usa la fecha actual como referencia.
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
def get_or_create_category(name: str) -> Dict[str, str]:
    """
    Crea una nueva categoría en la base de datos.
    Útil cuando un usuario registra un gasto con una categoría que no existe.
    Devuelve un diccionario con el resultado de la operación.
    """
    try:
        # Normalizar el nombre (primera letra mayúscula, resto minúsculas)
        normalized_name = name.strip().capitalize()

        # Verificar si ya existe
        if Category.objects.filter(name=normalized_name).exists():
            return {
                "status": "info",
                "message": f"La categoría '{normalized_name}' ya existe"
            }

        # Crear nueva categoría
        category, created = Category.objects.get_or_create(
            name=normalized_name)

        return {
            "status": "success",
            "message": f"Categoría '{normalized_name}' creada exitosamente",
            "id": str(category.id)
        }
    except Exception as e:
        logger.error(f"Error al crear categoría: {e}")
        return {
            "status": "error",
            "message": f"Error al crear categoría: {str(e)}"
        }


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

    # Normalizar el nombre de la categoría
    category_name = category.strip().capitalize()

    # Buscar la categoría en la base de datos
    category_obj = None
    try:
        category_obj = Category.objects.get(name=category_name)
    except Category.DoesNotExist:
        # Si no existe, solo usamos el nombre como category_str
        pass

    # Crear el texto para el embedding
    expense_text = f"Gasto de {amount} {currency} en {category_name} el {date}. {note}"

    # Generar embedding
    embedding = None
    try:
        embedding = embeddings.embed_query(expense_text)
    except Exception as e:
        logger.error(f"Error al generar embedding para gasto: {e}")

    expense = Expense.objects.create(
        user=user, amount=amount, currency=currency,
        category=category_obj, category_str=category_name,
        spent_at=date, note=note,
        timestamp=timezone.now(), embedding=embedding,
        raw_message=expense_text
    )
    return (
        f"✅ ¡Gasto registrado!\n"
        f"📊 Categoría: {category_name}\n"
        f"💰 Monto: {amount} {currency} {currency_message}\n"
        f"📅 Fecha: {date}\n"
        f"{'📝 Nota: ' + note if note else ''}"
    )
