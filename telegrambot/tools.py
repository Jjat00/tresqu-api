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
from income.models import Income, IncomeCategory
from users.models import User
from categories.models import Category, UserExpenseCategory, UserIncomeCategory
from categories.utils import get_or_create_user_expense_category, get_or_create_user_income_category
# serializasers
from .serializasers import ExpenseData, IncomeData
from .currencies import is_valid_currency
# Import embeddings service
from datetime import datetime, timedelta
from django.db import models
import pytz
import json

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
def get_current_date(user_external_id: str) -> str:
    """Devuelve la fecha actual en formato YYYY‑MM‑DD, considerando la zona horaria del usuario."""
    # Usar el ID de usuario para obtener su zona horaria
    try:
        user = User.objects.get(external_id=user_external_id)
        user_tz = pytz.timezone(user.timezone)
        return timezone.now().astimezone(user_tz).strftime("%Y-%m-%d")
    except (User.DoesNotExist, AttributeError, pytz.exceptions.UnknownTimeZoneError) as e:
        logger.warning(f"Error al obtener zona horaria del usuario: {e}")
        # Si hay error, usar UTC
        return timezone.now().strftime("%Y-%m-%d")


@tool
def parse_relative_date(date_text: str, user_external_id: str) -> str:
    """
    Convierte una referencia temporal relativa (ayer, el sábado, etc.) en una fecha específica.
    Toma como referencia la fecha actual en la zona horaria del usuario.
    Devuelve la fecha en formato YYYY-MM-DD.
    """
    # Obtener la fecha actual en la zona horaria del usuario
    try:
        user = User.objects.get(external_id=user_external_id)
        user_tz = pytz.timezone(user.timezone)
        today = timezone.now().astimezone(user_tz).replace(tzinfo=None)
    except (User.DoesNotExist, AttributeError, pytz.exceptions.UnknownTimeZoneError) as e:
        logger.warning(f"Error al obtener zona horaria del usuario: {e}")
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

        IMPORTANTE: Si no se menciona ninguna fecha específica en el mensaje,
        NO debes generar una fecha arbitraria. Deja el campo spent_at como NULL
        y el sistema usará automáticamente la fecha actual.

        IMPORTANTE: Todos los datos extraídos (categoría, nota) deben mantenerse
        en el mismo idioma en que fueron proporcionados por el usuario.
        Nunca traduzcas los nombres de categorías o notas a otro idioma.

        Por ejemplo:
        - "ayer compré un regalo a 20K" debe registrarse con la fecha de ayer
        - "el sábado gasté 100k en cervezas" debe registrarse con la fecha del sábado más reciente
        - "compré mi cena a 20k" debe usar la fecha actual
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

        IMPORTANTE: Si no se menciona ninguna fecha específica en el mensaje,
        NO debes generar una fecha arbitraria. Deja el campo spent_at como NULL
        y el sistema usará automáticamente la fecha actual.

        IMPORTANTE: Todos los datos extraídos (categoría, nota) deben mantenerse
        en el mismo idioma en que fueron proporcionados por el usuario.
        Nunca traduzcas los nombres de categorías o notas a otro idioma.
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
def get_or_create_category(name: str, description: str | None = None, examples: str | None = None, color: str | None = None) -> Dict[str, str]:
    """
    FUNCIÓN LEGACY: Crea una categoría GLOBAL en la base de datos.

    ⚠️ ESTA FUNCIÓN ESTÁ DEPRECATED Y SERÁ ELIMINADA.
    ⚠️ USA get_or_create_user_category_for_expense PARA NUEVOS DESARROLLOS.

    Útil cuando un usuario registra un gasto con una categoría que no existe.
    IMPORTANTE: Siempre usar el mismo idioma que está usando el usuario para el nombre, descripción y ejemplos.

    Args:
        name: Nombre de la categoría
        description: Descripción opcional de la categoría
        examples: Ejemplos opcionales de gastos para esta categoría
        color: Color hexadecimal opcional (formato: #RRGGBB)

    Devuelve un diccionario con el resultado de la operación.
    """
    try:
        # Normalizar el nombre manteniendo el formato título para consistencia
        normalized_name = name.strip().title()

        # Verificar si ya existe (case-insensitive)
        if Category.objects.filter(name__iexact=normalized_name).exists():
            category = Category.objects.get(name__iexact=normalized_name)
            # Actualizar si se proporcionan nuevos valores
            updated = False
            if description and not category.description:
                category.description = description
                updated = True
            if examples and not category.examples:
                category.examples = examples
                updated = True
            if color:
                category.color = color
                updated = True

            if updated:
                category.save()

            return {
                "status": "info",
                "message": f"La categoría '{normalized_name}' ya existe",
                "id": str(category.id),
                "color": category.color
            }

        # Si no hay color, dejamos que el modelo decida usando get_unused_color
        category_data = {
            "name": normalized_name,
        }

        if description:
            category_data["description"] = description

        if examples:
            category_data["examples"] = examples

        if color:
            category_data["color"] = color

        # Crear nueva categoría (el método save del modelo asignará un color si no se proporciona)
        category = Category.objects.create(**category_data)

        return {
            "status": "success",
            "message": f"Categoría '{normalized_name}' creada exitosamente",
            "id": str(category.id),
            "color": category.color
        }
    except Exception as e:
        logger.error(f"Error al crear categoría: {e}")
        return {
            "status": "error",
            "message": f"Error al crear categoría: {str(e)}"
        }


@tool
def get_or_create_income_category(name: str, description: str | None = None, example: str | None = None, color: str | None = None) -> Dict[str, str]:
    """
    Crea una nueva categoría de ingreso en la base de datos.
    Útil cuando un usuario registra un ingreso con una categoría que no existe.
    IMPORTANTE: Siempre usar el mismo idioma que está usando el usuario para el nombre, descripción y ejemplos.

    Args:
        name: Nombre de la categoría
        description: Descripción opcional de la categoría
        example: Ejemplos opcionales de ingresos para esta categoría
        color: Color hexadecimal opcional (formato: #RRGGBB)

    Devuelve un diccionario con el resultado de la operación.
    """
    try:
        from income.models import IncomeCategory

        # Normalizar el nombre manteniendo el formato título para consistencia
        normalized_name = name.strip().title()

        # Verificar si ya existe (insensible a mayúsculas/minúsculas)
        existing_category = IncomeCategory.objects.filter(
            name__iexact=normalized_name).first()
        if existing_category:
            category = existing_category

            # Actualizar si se proporcionan nuevos valores
            updated = False
            if description and hasattr(category, 'description') and not category.description:
                category.description = description
                updated = True
            if example and hasattr(category, 'example') and not category.example:
                category.example = example
                updated = True
            if color and hasattr(category, 'color'):
                category.color = color
                updated = True

            if updated:
                category.save()

            return {
                "status": "info",
                "message": f"La categoría de ingreso '{category.name}' ya existe",
                "id": str(category.id),
                "color": getattr(category, 'color', None)
            }

        # Crear nueva categoría
        category_data = {
            "name": normalized_name
        }

        # Agregar campos opcionales si el modelo los soporta
        if description and hasattr(IncomeCategory, 'description'):
            category_data["description"] = description

        if example and hasattr(IncomeCategory, 'example'):
            category_data["example"] = example

        if color and hasattr(IncomeCategory, 'color'):
            category_data["color"] = color

        category = IncomeCategory.objects.create(**category_data)

        return {
            "status": "success",
            "message": f"Categoría de ingreso '{normalized_name}' creada exitosamente",
            "id": str(category.id),
            "color": getattr(category, 'color', None)
        }
    except Exception as e:
        logger.error(f"Error al crear categoría de ingreso: {e}")
        return {
            "status": "error",
            "message": f"Error al crear categoría de ingreso: {str(e)}"
        }


# NUEVAS HERRAMIENTAS PARA CATEGORÍAS POR USUARIO

@tool
def get_or_create_user_category_for_expense(
    user_external_id: str,
    name: str,
    description: str | None = None,
    examples: str | None = None,
    color: str | None = None
) -> Dict[str, str]:
    """
    Crea o obtiene una categoría de gastos para un usuario específico.
    NUEVA FUNCIÓN: Reemplaza get_or_create_category para categorías por usuario.

    Args:
        user_external_id: ID externo del usuario
        name: Nombre de la categoría
        description: Descripción opcional de la categoría
        examples: Ejemplos opcionales de gastos para esta categoría
        color: Color hexadecimal opcional (formato: #RRGGBB)

    Devuelve un diccionario con el resultado de la operación.
    """
    try:
        user = User.objects.get(external_id=user_external_id)

        category, created = get_or_create_user_expense_category(
            user=user,
            name=name,
            description=description,
            examples=examples,
            color=color
        )

        status = "success" if created else "info"
        message = f"Categoría '{category.name}' {'creada' if created else 'ya existe'} para el usuario"

        return {
            "status": status,
            "message": message,
            "id": str(category.id),
            "color": category.color,
            "is_default": category.is_default
        }

    except User.DoesNotExist:
        logger.error(f"Usuario no encontrado: {user_external_id}")
        return {
            "status": "error",
            "message": f"Usuario no encontrado: {user_external_id}"
        }
    except Exception as e:
        logger.error(f"Error al crear categoría de gastos del usuario: {e}")
        return {
            "status": "error",
            "message": f"Error al crear categoría: {str(e)}"
        }


@tool
def get_or_create_user_category_for_income(
    user_external_id: str,
    name: str,
    description: str | None = None,
    example: str | None = None,
    color: str | None = None
) -> Dict[str, str]:
    """
    Crea o obtiene una categoría de ingresos para un usuario específico.
    NUEVA FUNCIÓN: Reemplaza get_or_create_income_category para categorías por usuario.

    Args:
        user_external_id: ID externo del usuario
        name: Nombre de la categoría
        description: Descripción opcional de la categoría
        example: Ejemplo opcional de ingresos para esta categoría
        color: Color hexadecimal opcional (formato: #RRGGBB)

    Devuelve un diccionario con el resultado de la operación.
    """
    try:
        user = User.objects.get(external_id=user_external_id)

        category, created = get_or_create_user_income_category(
            user=user,
            name=name,
            description=description,
            example=example,
            color=color
        )

        status = "success" if created else "info"
        message = f"Categoría de ingreso '{category.name}' {'creada' if created else 'ya existe'} para el usuario"

        return {
            "status": status,
            "message": message,
            "id": str(category.id),
            "color": category.color,
            "is_default": category.is_default
        }

    except User.DoesNotExist:
        logger.error(f"Usuario no encontrado: {user_external_id}")
        return {
            "status": "error",
            "message": f"Usuario no encontrado: {user_external_id}"
        }
    except Exception as e:
        logger.error(f"Error al crear categoría de ingresos del usuario: {e}")
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

    # Normalizar el nombre de la categoría para mostrar (primera letra mayúscula)
    display_category_name = category.strip().capitalize()

    # NUEVO: Buscar o crear categoría por usuario
    try:
        # Crear o obtener la categoría específica del usuario
        result = get_or_create_user_category_for_expense.invoke({
            "user_external_id": user_external_id,
            "name": display_category_name
        })

        if result["status"] in ["success", "info"]:
            # Obtener la categoría del usuario recién creada/encontrada
            user_category = UserExpenseCategory.objects.get(
                user=user,
                name=display_category_name.capitalize()
            )
            category_name = user_category.name
        else:
            # Si hubo un error al crear la categoría, crear una por defecto
            logger.error(f"Error al crear categoría para gasto: {result}")
            user_category, created = get_or_create_user_expense_category(
                user=user,
                name="Otros",
                description="Categoría por defecto para gastos sin clasificar",
                examples="Gastos varios, misceláneos"
            )
            category_name = "Otros"

    except Exception as e:
        logger.error(f"Error al buscar/crear categoría del usuario: {e}")
        # Crear una categoría por defecto para evitar gastos sin categoría
        user_category, created = get_or_create_user_expense_category(
            user=user,
            name="Otros",
            description="Categoría por defecto para gastos sin clasificar",
            examples="Gastos varios, misceláneos"
        )
        category_name = "Otros"

    # Crear el texto para el embedding
    expense_text = f"Gasto de {amount} {currency} en {category_name} el {date}. {note}"

    # Generar embedding
    embedding = None
    try:
        embedding = embeddings.embed_query(expense_text)
    except Exception as e:
        logger.error(f"Error al generar embedding para gasto: {e}")

    # NUEVO: Crear gasto con categoría por usuario
    expense = Expense.objects.create(
        user=user,
        amount=amount,
        currency=currency,
        category=None,  # DEJAR EN NULL - No usar categoría global
        category_str=category_name,
        user_expense_category=user_category,  # USAR NUEVA CATEGORÍA POR USUARIO
        spent_at=date,
        note=note,
        timestamp=timezone.now(),
        embedding=embedding,
        raw_message=expense_text
    )
    return (
        f"✅ ¡Gasto registrado!\n"
        f"📊 Categoría: {category_name}\n"
        f"💰 Monto: {amount} {currency} {currency_message}\n"
        f"📅 Fecha: {date}\n"
        f"{'📝 Nota: ' + note if note else ''}"
    )


@tool
def update_expense(expense_id: str, amount: float, currency: str, category: str, spent_at: str | None = None, note: str | None = ""):
    """Actualiza un gasto en la base de datos."""
    try:
        expense = Expense.objects.get(id=expense_id)

        # Normalizar el nombre de la categoría para mostrar (primera letra mayúscula)
        display_category_name = category.strip().capitalize()

        # Buscar la categoría de forma insensible a mayúsculas/minúsculas
        try:
            # Buscar categoría existente (case-insensitive)
            category_obj = Category.objects.filter(
                name__iexact=category.strip()).first()
            if category_obj:
                # Si existe, usar el nombre exacto de la categoría existente
                category_name = category_obj.name
            else:
                # Si no existe, la vamos a crear con normalize_name
                category_name = display_category_name
                # Crear la categoría
                result = get_or_create_category.invoke({"name": category_name})
                if result["status"] == "success" or result["status"] == "info":
                    # Obtener la categoría recién creada
                    category_obj = Category.objects.get(name=category_name)
                else:
                    # Si hubo un error al crear la categoría, registrar el error
                    logger.error(
                        f"Error al crear categoría para gasto: {result}")
                    # Crear una categoría por defecto para evitar gastos sin categoría
                    category_obj, created = Category.objects.get_or_create(name="Otros",
                                                                           defaults={"description": "Categoría por defecto para gastos sin clasificar",
                                                                                     "examples": "Gastos varios, misceláneos"})
                    category_name = "Otros"
        except Exception as e:
            logger.error(f"Error al buscar/crear categoría: {e}")
            # Crear una categoría por defecto para evitar gastos sin categoría
            category_obj, created = Category.objects.get_or_create(name="Otros",
                                                                   defaults={"description": "Categoría por defecto para gastos sin clasificar",
                                                                             "examples": "Gastos varios, misceláneos"})
            category_name = "Otros"

        expense.amount = amount
        expense.currency = currency
        expense.category = category_obj
        expense.category_str = category_name
        if spent_at:
            expense.spent_at = datetime.strptime(spent_at, "%Y-%m-%d").date()
        if note:
            expense.note = note

        # Actualizar el raw_message
        expense_text = f"Gasto de {amount} {currency} en {category_name} el {expense.spent_at}. {note}"
        expense.raw_message = expense_text  # Usar texto plano en lugar de JSON

        # Actualizar embedding
        try:
            expense.embedding = embeddings.embed_query(expense_text)
        except Exception as e:
            logger.error(f"Error actualizando embedding para gasto: {e}")

        expense.save()
        return f"✅ ¡Gasto actualizado!\n"
    except Exception as e:
        logger.error(f"Error al actualizar gasto: {e}")
        return f"❌ Error al actualizar el gasto: {str(e)}"


@tool
def delete_expense(expense_id: str):
    """Elimina un gasto de la base de datos."""
    try:
        expense = Expense.objects.get(id=expense_id)
        expense.delete()
        return f"✅ ¡Gasto eliminado!\n"
    except Exception as e:
        logger.error(f"Error al eliminar gasto: {e}")
        return f"❌ Error al eliminar el gasto: {str(e)}"


@tool
def get_expenses_by_user(user_external_id: str):
    """Obtiene todos los gastos de un usuario."""
    try:
        user = User.objects.get(external_id=user_external_id)
        expenses = Expense.objects.filter(user=user)
        return [{
            'id': str(expense.id),
            'amount': expense.amount,
            'currency': expense.currency,
            'category': expense.category_str,
            'spent_at': expense.spent_at.strftime('%Y-%m-%d'),
            'note': expense.note,
            'raw_message': expense.raw_message
        } for expense in expenses]
    except Exception as e:
        logger.error(f"Error al obtener gastos: {e}")
        return []


@tool
def get_expense_by_id(expense_id: str):
    """Obtiene un gasto por su ID."""
    try:
        expense = Expense.objects.get(id=expense_id)
        return {
            'id': str(expense.id),
            'amount': expense.amount,
            'currency': expense.currency,
            'category': expense.category_str,
            'spent_at': expense.spent_at.strftime('%Y-%m-%d'),
            'note': expense.note,
            'raw_message': expense.raw_message
        }
    except Exception as e:
        logger.error(f"Error al obtener gasto: {e}")
        return None


@tool
def search_expenses_by_text(user_external_id: str, search_text: str) -> List[Dict[str, Any]]:
    """
    Busca gastos que coincidan con el texto de búsqueda usando embeddings.
    """
    try:
        user = User.objects.get(external_id=user_external_id)

        # Generamos embedding del texto de búsqueda
        search_embedding = embeddings.embed_query(search_text)

        # Buscamos gastos similares
        similar_expenses = Expense.find_similar(user, search_embedding)

        return [{
            'id': str(expense.id),
            'amount': expense.amount,
            'currency': expense.currency,
            'category': expense.category_str,
            'spent_at': expense.spent_at.strftime('%Y-%m-%d'),
            'note': expense.note,
            'raw_message': expense.raw_message
        } for expense in similar_expenses]
    except Exception as e:
        logger.error(f"Error buscando gastos por texto: {e}")
        return []


@tool
def get_expenses_by_category(user_external_id: str, category: str, start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
    """
    Obtiene los gastos de una categoría específica en un rango de fechas.
    Si no se especifican fechas, devuelve todos los gastos de la categoría.
    """
    try:
        user = User.objects.get(external_id=user_external_id)
        query = Expense.objects.filter(
            user=user, category_str__iexact=category)

        if start_date:
            query = query.filter(spent_at__gte=start_date)
        if end_date:
            query = query.filter(spent_at__lte=end_date)

        # Calcular el total
        total = query.aggregate(total=models.Sum('amount'))['total'] or 0

        # Obtener los gastos
        expenses = query.order_by('-spent_at')

        return {
            'total': float(total),
            'currency': user.default_currency,
            'expenses': [{
                'id': str(expense.id),
                'amount': float(expense.amount),
                'currency': expense.currency,
                'spent_at': expense.spent_at.strftime('%Y-%m-%d'),
                'note': expense.note,
                'raw_message': expense.raw_message
            } for expense in expenses]
        }
    except Exception as e:
        logger.error(f"Error al obtener gastos por categoría: {e}")
        return {'error': str(e)}


@tool
def get_top_categories(user_external_id: str, start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
    """
    Obtiene las categorías con mayores gastos en un rango de fechas.
    Si no se especifican fechas, devuelve todas las categorías.
    """
    try:
        user = User.objects.get(external_id=user_external_id)
        query = Expense.objects.filter(user=user)

        if start_date:
            query = query.filter(spent_at__gte=start_date)
        if end_date:
            query = query.filter(spent_at__lte=end_date)

        # Agrupar por categoría y sumar montos
        categories = query.values('category_str').annotate(
            total=models.Sum('amount')
        ).order_by('-total')

        return [{
            'category': cat['category_str'],
            'total': float(cat['total']),
            'currency': user.default_currency
        } for cat in categories]
    except Exception as e:
        logger.error(f"Error al obtener top categorías: {e}")
        return {'error': str(e)}


class IncomeList(BaseModel):
    """Lista de ingresos detectados en un solo mensaje."""
    incomes: List[IncomeData]


@tool()
async def parse_income(text: str) -> dict:
    """
    Analiza un mensaje para extraer un ingreso.
    Devuelve dict o {'error': …} si no hay datos suficientes.
    """
    structured_llm = llm.with_structured_output(
        IncomeData, method="function_calling")

    prompt = ChatPromptTemplate.from_messages([
        ("system", """
        Eres un asistente financiero experto en extraer información de ingresos.
        Extrae un ingreso y devuélvelo como IncomeData.
        Si el texto menciona un ingreso, extrae la información solicitada.
        Si no hay información suficiente, haz tu mejor estimación.
        Si el texto no menciona ningún ingreso (como saludos), genera un error.
        
        Si el mensaje incluye referencias temporales como "ayer", "el martes", 
        "la semana pasada", etc., debes identificarlas correctamente para establecer
        la fecha del ingreso. Usa la fecha actual como referencia.
        
        IMPORTANTE: Si no se menciona ninguna fecha específica en el mensaje, 
        NO debes generar una fecha arbitraria. Deja el campo received_at como NULL 
        y el sistema usará automáticamente la fecha actual.
        
        IMPORTANTE: Todos los datos extraídos (categoría, nota) deben mantenerse
        en el mismo idioma en que fueron proporcionados por el usuario.
        Nunca traduzcas los nombres de categorías o notas a otro idioma.
        
        Por ejemplo:
        - "ayer recibí un pago a 20K" debe registrarse con la fecha de ayer
        - "el sábado me pagaron 100k" debe registrarse con la fecha del sábado más reciente
        - "recibí mi sueldo de 2M" debe dejarse sin fecha (null) para usar la fecha actual automáticamente
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
async def parse_incomes(text: str) -> Dict[str, Any]:
    """Extrae VARIOS ingresos."""
    chain = (ChatPromptTemplate.from_messages([
        ("system", """
        Eres un asistente financiero experto en extraer información de ingresos.
        Extrae todos los ingresos y devuélvelos como una lista de IncomeData.
        Si el texto menciona ingresos, extrae la información solicitada.
        Si no hay información suficiente, haz tu mejor estimación.
        Si el texto no menciona ningún ingreso (como saludos), genera un error.
        
        Si el mensaje incluye referencias temporales como "ayer", "el martes", 
        "la semana pasada", etc., debes identificarlas correctamente para establecer
        la fecha del ingreso. Usa la fecha actual como referencia.
        
        IMPORTANTE: Si no se menciona ninguna fecha específica en el mensaje, 
        NO debes generar una fecha arbitraria. Deja el campo received_at como NULL 
        y el sistema usará automáticamente la fecha actual.

        IMPORTANTE: Todos los datos extraídos (categoría, nota) deben mantenerse
        en el mismo idioma en que fueron proporcionados por el usuario.
        Nunca traduzcas los nombres de categorías o notas a otro idioma.
        """),
        ("human", "{text}")
    ]) | llm.with_structured_output(
        IncomeList, method="function_calling"))
    try:
        result: IncomeList = await chain.ainvoke({"text": text})
        return result.model_dump()
    except Exception as e:
        return {"error": str(e)}


@tool
def create_income(
    user_external_id: str,
    amount: float,
    currency: str,
    category: str,
    received_at: str | None = None,
    note: str | None = ""
) -> str:
    """
    Registra un ingreso para el usuario especificado.
    Si no se proporciona moneda o no es válida, usa la moneda por defecto del usuario.

    Args:
        user_external_id: ID externo del usuario
        amount: Cantidad de ingreso
        currency: Moneda (USD, EUR, COP, etc.)
        category: Categoría del ingreso
        received_at: Fecha en formato YYYY-MM-DD (opcional)
        note: Nota adicional (opcional)

    Returns:
        Mensaje de confirmación
    """
    try:
        from income.models import Income, IncomeCategory
        from users.models import User
        from django.utils import timezone
        import json
        from datetime import datetime

        # Obtener el usuario
        user = User.objects.filter(external_id=user_external_id).first()
        if not user:
            return f"Error: Usuario no encontrado."

        # Por seguridad limitamos los valores aceptados
        if amount <= 0:
            return f"Error: El monto del ingreso debe ser positivo."

        # Verificar si la moneda es válida, de lo contrario usar la moneda por defecto del usuario
        currency_message = ""
        if not currency or not is_valid_currency(currency):
            # Guardar la original por si se quiere informar al usuario
            original_currency = currency
            currency = user.default_currency
            # Solo mostrar mensaje si el usuario intentó especificar una moneda inválida
            if original_currency and original_currency.strip() != "":
                currency_message = f"(Moneda '{original_currency}' no válida, se usó tu moneda por defecto: {currency})"
            else:  # Si no especificó moneda, el mensaje es más simple
                currency_message = f"(Se usó tu moneda por defecto: {currency})"
        else:
            currency = currency.upper()  # Asegurar que la moneda válida esté en mayúsculas

        # Normalizar el nombre de la categoría (primera letra mayúscula de cada palabra)
        normalized_category = " ".join(word.capitalize()
                                       for word in category.strip().split())

        # NUEVO: Buscar o crear categoría de ingreso por usuario
        try:
            # Crear o obtener la categoría específica del usuario
            result = get_or_create_user_category_for_income.invoke({
                "user_external_id": user_external_id,
                "name": normalized_category
            })

            if result["status"] in ["success", "info"]:
                # Obtener la categoría del usuario recién creada/encontrada
                user_income_category = UserIncomeCategory.objects.get(
                    user=user,
                    name=normalized_category
                )
            else:
                # Si hubo un error al crear la categoría, crear una por defecto
                logger.error(f"Error al crear categoría de ingreso: {result}")
                user_income_category, created = get_or_create_user_income_category(
                    user=user,
                    name="Otros Ingresos",
                    description="Categoría por defecto para ingresos sin clasificar",
                    example="Ingresos varios, misceláneos"
                )

        except Exception as e:
            logger.error(
                f"Error al buscar/crear categoría de ingreso del usuario: {e}")
            # Crear una categoría por defecto para evitar ingresos sin categoría
            user_income_category, created = get_or_create_user_income_category(
                user=user,
                name="Otros Ingresos",
                description="Categoría por defecto para ingresos sin clasificar",
                example="Ingresos varios, misceláneos"
            )

        # Fecha de recepción
        if received_at:
            try:
                received_date = datetime.strptime(
                    received_at, "%Y-%m-%d").date()
            except ValueError:
                received_date = timezone.now().date()
        else:
            received_date = timezone.now().date()

        # NUEVO: Crear el objeto Income con categoría por usuario
        income_text = f"Ingreso de {amount} {currency} en {user_income_category.name} el {received_date}. {note}"
        income = Income.objects.create(
            user=user,
            amount=amount,
            currency=currency,
            category=None,  # DEJAR EN NULL - No usar categoría global
            category_str=user_income_category.name,
            user_income_category=user_income_category,  # USAR NUEVA CATEGORÍA POR USUARIO
            timestamp=timezone.now(),
            received_at=received_date,
            note=note,
            raw_message=income_text
        )

        # Generar embedding para la búsqueda semántica
        try:
            income.embedding = embeddings.embed_query(income_text)
            income.save()
        except Exception as e:
            logger.error(f"Error generando embedding para ingreso: {e}")

        return f"Ingreso registrado: {amount} {currency} en {user_income_category.name} ({received_date}) {currency_message}".strip()

    except Exception as e:
        return f"Error al registrar el ingreso: {str(e)}"


@tool
def update_income(income_id: str, amount: float, currency: str, category: str, received_at: str | None = None, note: str | None = ""):
    """
    Actualiza un ingreso existente.
    """
    try:
        from income.models import Income, IncomeCategory
        from datetime import datetime
        import json

        income = Income.objects.filter(id=income_id).first()
        if not income:
            return f"Error: Ingreso con ID {income_id} no encontrado."

        # Normalizar el nombre de la categoría (primera letra mayúscula de cada palabra)
        normalized_category = " ".join(word.capitalize()
                                       for word in category.strip().split())

        # Obtener o crear la categoría (búsqueda insensible a mayúsculas/minúsculas)
        category_obj = IncomeCategory.objects.filter(
            name__iexact=normalized_category).first()
        if not category_obj:
            # Si no existe, la creamos
            category_obj = IncomeCategory.objects.create(
                name=normalized_category)

        income.amount = amount
        income.currency = currency
        income.category = category_obj
        income.category_str = category_obj.name  # Usar el nombre normalizado

        if received_at:
            try:
                income.received_at = datetime.strptime(
                    received_at, "%Y-%m-%d").date()
            except ValueError:
                pass

        income.note = note

        # Actualizar el raw_message
        income_text = f"Ingreso de {amount} {currency} en {category_obj.name} el {income.received_at}. {note}"
        income.raw_message = income_text  # Usar texto plano en lugar de JSON

        # Actualizar embedding
        try:
            income.embedding = embeddings.embed_query(income_text)
        except Exception as e:
            logger.error(f"Error actualizando embedding para ingreso: {e}")

        income.save()
        return f"Ingreso actualizado: {amount} {currency} en {category_obj.name}"

    except Exception as e:
        return f"Error al actualizar el ingreso: {str(e)}"


@tool
def delete_income(income_id: str):
    """
    Elimina un ingreso por su ID.
    """
    try:
        from income.models import Income
        income = Income.objects.filter(id=income_id).first()
        if not income:
            return f"Error: Ingreso con ID {income_id} no encontrado."

        details = f"{income.amount} {income.currency} en {income.category_str if income.category_str else (income.category.name if income.category else 'Sin categoría')}"
        income.delete()
        return f"Ingreso eliminado: {details}"
    except Exception as e:
        return f"Error al eliminar el ingreso: {str(e)}"


@tool
def get_incomes_by_user(user_external_id: str, start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
    """
    Obtiene todos los ingresos de un usuario en un rango de fechas.
    """
    try:
        from income.models import Income
        from users.models import User
        from datetime import datetime

        user = User.objects.filter(external_id=user_external_id).first()
        if not user:
            return []

        query = Income.objects.filter(user=user)

        if start_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                query = query.filter(received_at__gte=start)
            except ValueError:
                pass

        if end_date:
            try:
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
                query = query.filter(received_at__lte=end)
            except ValueError:
                pass

        incomes = query.order_by('-received_at')
        return [
            {
                "id": str(income.id),
                "amount": float(income.amount),
                "currency": income.currency,
                "category": income.category.name if income.category else income.category_str,
                "received_at": income.received_at.strftime("%Y-%m-%d") if income.received_at else None,
                "note": income.note
            }
            for income in incomes
        ]
    except Exception as e:
        logger.error(f"Error obteniendo ingresos del usuario: {e}")
        return []


@tool
def get_income_by_id(income_id: str) -> Dict[str, Any]:
    """
    Obtiene los detalles de un ingreso por su ID.
    """
    try:
        from income.models import Income
        income = Income.objects.filter(id=income_id).first()
        if not income:
            return {"error": f"Ingreso con ID {income_id} no encontrado."}

        return {
            "id": str(income.id),
            "amount": float(income.amount),
            "currency": income.currency,
            "category": income.category.name if income.category else income.category_str,
            "received_at": income.received_at.strftime("%Y-%m-%d") if income.received_at else None,
            "note": income.note
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def search_incomes_by_text(user_external_id: str, search_text: str) -> List[Dict[str, Any]]:
    """
    Busca ingresos que coincidan con el texto de búsqueda.
    """
    try:
        from income.models import Income
        from users.models import User

        user = User.objects.filter(external_id=user_external_id).first()
        if not user:
            return []

        # Generamos embedding del texto de búsqueda
        search_embedding = embeddings.embed_query(search_text)

        # Buscamos ingresos similares
        similar_incomes = Income.find_similar(user, search_embedding)

        return [
            {
                "id": str(income.id),
                "amount": float(income.amount),
                "currency": income.currency,
                "category": income.category.name if income.category else income.category_str,
                "received_at": income.received_at.strftime("%Y-%m-%d") if income.received_at else None,
                "note": income.note
            }
            for income in similar_incomes
        ]
    except Exception as e:
        logger.error(f"Error buscando ingresos por texto: {e}")
        return []


@tool
def get_incomes_by_category(user_external_id: str, category: str, start_date: str | None = None, end_date: str | None = None) -> Dict[str, Any]:
    """
    Obtiene los ingresos de una categoría específica en un rango de fechas.
    """
    try:
        from income.models import Income, IncomeCategory
        from users.models import User
        from datetime import datetime
        from django.db.models import Sum

        user = User.objects.filter(external_id=user_external_id).first()
        if not user:
            return {"error": "Usuario no encontrado"}

        # Buscar la categoría
        categories = IncomeCategory.objects.filter(name__icontains=category)
        if not categories.exists():
            return {"error": f"Categoría {category} no encontrada"}

        # Preparar filtros
        query = Income.objects.filter(
            user=user,
            category__in=categories
        )

        if start_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                query = query.filter(received_at__gte=start)
            except ValueError:
                pass

        if end_date:
            try:
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
                query = query.filter(received_at__lte=end)
            except ValueError:
                pass

        # Calcular total y obtener ingresos
        total = query.aggregate(Sum('amount'))['amount__sum'] or 0
        incomes = query.order_by('-received_at')

        return {
            "category": category,
            "total": float(total),
            "currency": user.default_currency,
            "incomes": [
                {
                    "id": str(income.id),
                    "amount": float(income.amount),
                    "currency": income.currency,
                    "received_at": income.received_at.strftime("%Y-%m-%d") if income.received_at else None,
                    "note": income.note
                }
                for income in incomes
            ]
        }
    except Exception as e:
        logger.error(f"Error obteniendo ingresos por categoría: {e}")
        return {"error": str(e)}


@tool
def get_top_income_categories(user_external_id: str, start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
    """
    Obtiene las categorías con mayores ingresos en un rango de fechas.
    """
    try:
        from income.models import Income
        from users.models import User
        from datetime import datetime
        from django.db.models import Sum

        user = User.objects.filter(external_id=user_external_id).first()
        if not user:
            return []

        # Preparar filtros
        query = Income.objects.filter(user=user)

        if start_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                query = query.filter(received_at__gte=start)
            except ValueError:
                pass

        if end_date:
            try:
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
                query = query.filter(received_at__lte=end)
            except ValueError:
                pass

        # Agrupar por categoría y sumar montos
        result = []
        categories = query.values(
            'category__name', 'category_str'
        ).annotate(
            total=Sum('amount')
        ).order_by('-total')

        for cat in categories:
            category_name = cat['category__name'] or cat['category_str'] or 'Sin categoría'
            result.append({
                "category": category_name,
                "total": float(cat['total']),
                "currency": user.default_currency
            })

        return result
    except Exception as e:
        logger.error(f"Error obteniendo top categorías de ingresos: {e}")
        return []
