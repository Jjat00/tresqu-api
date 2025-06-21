from asgiref.sync import sync_to_async
from users.models import Message
from langchain.schema import HumanMessage, AIMessage
from categories.models import Category
from income.models import IncomeCategory
# NUEVO: Importar utilidades para categorías por usuario
from categories.utils import (
    get_user_expense_categories,
    get_user_income_categories,
    get_user_categories_with_details,
    get_expense_categories_hybrid,
    get_income_categories_hybrid,
    get_categories_with_details_hybrid
)


async def fetch_last_messages(user_id: int, window: int = 10):
    """
    Recupera los últimos mensajes para un usuario específico y los convierte
    al formato adecuado para LangChain.
    """
    def _query():
        return list(
            Message.objects
            .filter(chat__user_id=user_id)
            .order_by("-created_at")[:window]
        )
    records = await sync_to_async(_query, thread_sensitive=True)()
    # del más viejo al más nuevo
    for r in reversed(records):
        if r.message_type == "incoming":
            yield HumanMessage(content=r.text)
        else:
            yield AIMessage(content=r.text)


def normalize_category_name(name: str) -> str:
    """Normaliza el nombre de una categoría"""
    return name.strip().lower()


def normalize_phone_number(phone_number):
    """
    Normaliza un número de teléfono eliminando el signo + al inicio y todos los espacios.
    Maneja casos especiales como números mexicanos.

    Args:
        phone_number (str): El número de teléfono a normalizar

    Returns:
        str: El número normalizado sin el signo + y sin espacios, o None si el input era None

    Examples:
        "+52 55 2899 5412" -> "5215528995412" (México móvil)
        "+5215528995412" -> "5215528995412" 
        "5215528995412" -> "5215528995412"
        "+52 55 2899 5412" -> "5215528995412"
    """
    if not phone_number:
        return None

    # Eliminar espacios al inicio y final primero
    normalized = phone_number.strip()

    # Eliminar el signo + al inicio si existe
    normalized = normalized.lstrip('+')

    # Eliminar todos los espacios, guiones y otros caracteres
    normalized = normalized.replace(' ', '').replace(
        '-', '').replace('(', '').replace(')', '')

    # Caso especial para México: números móviles
    # Si el número empieza con 52 y tiene 12 dígitos, pero no tiene el "1" después del código de país
    # Ejemplo: 525528995412 debería ser 5215528995412
    if normalized.startswith('52') and len(normalized) == 12:
        # Verificar si es un número móvil mexicano (códigos de área móviles comunes)
        # Los códigos de área móviles en México incluyen: 55, 33, 81, 222, etc.
        # Obtener los primeros 2 dígitos después de 52
        area_codes = normalized[2:4]
        mobile_area_codes = ['55', '33', '81', '22', '44',
                             '66', '99', '77', '61', '64', '65', '67', '68', '69']

        if area_codes in mobile_area_codes:
            # Insertar el "1" después del código de país para números móviles
            normalized = '521' + normalized[2:]

    return normalized


@sync_to_async
def get_existing_categories(user=None):
    """
    Obtiene la lista de todas las categorías disponibles para gastos.
    ACTUALIZADA: Ahora soporta categorías por usuario.

    Args:
        user: Usuario específico (opcional). Si no se proporciona, usa categorías globales.
    """
    try:
        if user:
            return get_user_expense_categories(user)
        else:
            # Fallback a categorías globales (para compatibilidad)
            categories = Category.get_all_categories()
            return categories
    except Exception as e:
        print(f"Error al obtener categorías: {e}")
        return []


@sync_to_async
def get_existing_income_categories(user=None):
    """
    Obtiene la lista de todas las categorías disponibles para ingresos.
    ACTUALIZADA: Ahora soporta categorías por usuario.

    Args:
        user: Usuario específico (opcional). Si no se proporciona, usa categorías globales.
    """
    try:
        if user:
            return get_user_income_categories(user)
        else:
            # Fallback a categorías globales (para compatibilidad)
            categories = IncomeCategory.get_all_categories()
            return categories
    except Exception as e:
        print(f"Error al obtener categorías de ingresos: {e}")
        return []


@sync_to_async
def get_categories_with_details(user=None):
    """
    Obtiene un diccionario con todas las categorías disponibles para gastos e ingresos
    incluyendo su descripción y ejemplos.
    ACTUALIZADA: Ahora soporta categorías por usuario.

    Args:
        user: Usuario específico (opcional). Si no se proporciona, usa categorías globales.

    Returns:
        dict: Diccionario donde cada clave es el nombre de la categoría y cada valor
              es otro diccionario con 'description', 'examples' y 'color'.
    """
    try:
        if user:
            # Obtener categorías del usuario y convertir al formato esperado
            user_categories = get_user_categories_with_details(user)
            result = {}

            # Procesar categorías de gastos
            for category in user_categories['expense_categories']:
                result[category['name']] = {
                    'description': category['description'],
                    'examples': category['examples'],
                    'color': category['color'],
                    'type': 'expense'
                }

            # Procesar categorías de ingresos
            for category in user_categories['income_categories']:
                result[category['name']] = {
                    'description': category['description'],
                    # Nota: 'example' en singular para ingresos
                    'examples': category['example'],
                    'color': category['color'],
                    'type': 'income'
                }

            return result
        else:
            # Fallback a lógica original (categorías globales)
            # Obtener categorías de gastos
            expense_categories = Category.objects.all().values(
                'name', 'description', 'examples', 'color')

            # Obtener categorías de ingresos
            income_categories = IncomeCategory.objects.all().values(
                'name', 'description', 'example', 'color')

            result = {}

            # Procesar categorías de gastos
            for category in expense_categories:
                result[category['name']] = {
                    'description': category['description'] or '',
                    'examples': category['examples'] or '',
                    'color': category['color'],
                    'type': 'expense'
                }

            # Procesar categorías de ingresos
            for category in income_categories:
                result[category['name']] = {
                    'description': category['description'] or '',
                    # Notar que aquí es 'example' en singular
                    'examples': category['example'] or '',
                    'color': category['color'],
                    'type': 'income'
                }

            return result
    except Exception as e:
        print(f"Error al obtener categorías con detalles: {e}")
        return {}


# NUEVAS FUNCIONES PARA CATEGORÍAS POR USUARIO

@sync_to_async
def get_user_expense_categories_async(user):
    """
    Versión asíncrona para obtener categorías de gastos de un usuario específico.
    """
    try:
        return get_user_expense_categories(user)
    except Exception as e:
        print(f"Error al obtener categorías de gastos del usuario: {e}")
        return []


@sync_to_async
def get_user_income_categories_async(user):
    """
    Versión asíncrona para obtener categorías de ingresos de un usuario específico.
    """
    try:
        return get_user_income_categories(user)
    except Exception as e:
        print(f"Error al obtener categorías de ingresos del usuario: {e}")
        return []


@sync_to_async
def get_user_categories_with_details_async(user):
    """
    Versión asíncrona para obtener categorías del usuario con detalles.
    """
    try:
        return get_user_categories_with_details(user)
    except Exception as e:
        print(f"Error al obtener categorías del usuario con detalles: {e}")
        return {}
