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
from .serializasers import ExpenseData, IncomeData
from .currencies import is_valid_currency
# Import embeddings service
from datetime import datetime, timedelta
from django.db import models

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
    # Asegurémonos de usar timezone para tener en cuenta la zona horaria configurada
    return timezone.now().strftime("%Y-%m-%d")


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
        
        IMPORTANTE: Si no se menciona ninguna fecha específica en el mensaje, 
        NO debes generar una fecha arbitraria. Deja el campo spent_at como NULL 
        y el sistema usará automáticamente la fecha actual.
        
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


@tool
def update_expense(expense_id: str, amount: float, currency: str, category: str, spent_at: str | None = None, note: str | None = ""):
    """Actualiza un gasto en la base de datos."""
    try:
        expense = Expense.objects.get(id=expense_id)

        # Normalizar el nombre de la categoría
        category_name = category.strip().capitalize()

        # Buscar o crear la categoría
        category_obj = None
        try:
            category_obj = Category.objects.get(name=category_name)
        except Category.DoesNotExist:
            # Si no existe, solo usamos el nombre como category_str
            pass

        expense.amount = amount
        expense.currency = currency
        expense.category = category_obj
        expense.category_str = category_name
        if spent_at:
            expense.spent_at = datetime.strptime(spent_at, "%Y-%m-%d").date()
        if note:
            expense.note = note

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
    Busca gastos que coincidan con el texto de búsqueda.
    Devuelve una lista de gastos que coinciden con el texto.
    """
    user = User.objects.get(external_id=user_external_id)

    # Buscar en el texto del mensaje original y en la nota
    expenses = Expense.objects.filter(
        user=user,
        raw_message__icontains=search_text
    ) | Expense.objects.filter(
        user=user,
        note__icontains=search_text
    )

    # Ordenar por fecha más reciente
    expenses = expenses.order_by('-spent_at', '-timestamp')

    # Convertir a lista de diccionarios
    return [{
        'id': str(expense.id),
        'amount': expense.amount,
        'currency': expense.currency,
        'category': expense.category_str,
        'spent_at': expense.spent_at.strftime('%Y-%m-%d'),
        'note': expense.note,
        'raw_message': expense.raw_message
    } for expense in expenses[:5]]  # Limitar a 5 resultados


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
        from income.models import Income
        from users.models import User
        from categories.models import Category
        from django.utils import timezone
        import json
        from datetime import datetime

        # Por seguridad limitamos los valores aceptados
        if amount <= 0:
            return f"Error: El monto del ingreso debe ser positivo."

        if not is_valid_currency(currency):
            return f"Error: Moneda {currency} no reconocida."

        # Obtener el usuario
        user = User.objects.filter(external_id=user_external_id).first()
        if not user:
            return f"Error: Usuario no encontrado."

        # Obtener o crear la categoría
        category_obj, created = Category.objects.get_or_create(
            name=category.title())

        # Fecha de recepción
        if received_at:
            try:
                received_date = datetime.strptime(
                    received_at, "%Y-%m-%d").date()
            except ValueError:
                received_date = timezone.now().date()
        else:
            received_date = timezone.now().date()

        # Crear el objeto Income
        income = Income.objects.create(
            user=user,
            amount=amount,
            currency=currency,
            category=category_obj,
            category_str=category.title(),
            timestamp=timezone.now(),
            received_at=received_date,
            note=note,
            raw_message=json.dumps({
                "amount": amount,
                "currency": currency,
                "category": category,
                "received_at": received_at,
                "note": note
            })
        )

        # Generar embedding para la búsqueda semántica
        try:
            search_text = f"{category} {note} {amount} {currency} {received_at}"
            income.embedding = embeddings.embed_query(search_text)
            income.save()
        except Exception as e:
            logger.error(f"Error generando embedding para ingreso: {e}")

        return f"Ingreso registrado: {amount} {currency} en {category.title()} ({received_date})"

    except Exception as e:
        return f"Error al registrar el ingreso: {str(e)}"


@tool
def update_income(income_id: str, amount: float, currency: str, category: str, received_at: str | None = None, note: str | None = ""):
    """
    Actualiza un ingreso existente.
    """
    try:
        from income.models import Income
        from categories.models import Category
        from datetime import datetime
        import json

        income = Income.objects.filter(id=income_id).first()
        if not income:
            return f"Error: Ingreso con ID {income_id} no encontrado."

        category_obj, created = Category.objects.get_or_create(
            name=category.title())

        income.amount = amount
        income.currency = currency
        income.category = category_obj
        income.category_str = category.title()

        if received_at:
            try:
                income.received_at = datetime.strptime(
                    received_at, "%Y-%m-%d").date()
            except ValueError:
                pass

        income.note = note

        # Actualizar el raw_message
        income.raw_message = json.dumps({
            "amount": amount,
            "currency": currency,
            "category": category,
            "received_at": received_at,
            "note": note
        })

        # Actualizar embedding
        try:
            search_text = f"{category} {note} {amount} {currency} {received_at}"
            income.embedding = embeddings.embed_query(search_text)
        except Exception as e:
            logger.error(f"Error actualizando embedding para ingreso: {e}")

        income.save()
        return f"Ingreso actualizado: {amount} {currency} en {category.title()}"

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
        from income.models import Income
        from users.models import User
        from categories.models import Category
        from datetime import datetime
        from django.db.models import Sum

        user = User.objects.filter(external_id=user_external_id).first()
        if not user:
            return {"error": "Usuario no encontrado"}

        # Buscar la categoría
        categories = Category.objects.filter(name__icontains=category)
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
