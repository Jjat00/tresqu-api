from asgiref.sync import sync_to_async
from users.models import Message
from langchain_core.messages import HumanMessage, AIMessage
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
    Maneja casos especiales como números mexicanos, añadiendo el "1" requerido por WhatsApp.

    Args:
        phone_number (str): El número de teléfono a normalizar

    Returns:
        str: El número normalizado sin el signo + y sin espacios, o None si el input era None

    Examples:
        "+52 55 2899 5412" -> "5215528995412" (México móvil)
        "+5215528995412" -> "5215528995412" (ya correcto)
        "525528995412" -> "5215528995412" (añade el 1)
        "5215528995412" -> "5215528995412" (ya correcto)
        "+52 33 1234 5678" -> "5213312345678" (Guadalajara)

    Nota: Para números mexicanos, WhatsApp requiere el formato 521XXXXXXXXXX
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
    # WhatsApp requiere el formato 521XXXXXXXXXX para números mexicanos
    if normalized.startswith('52'):
        # Lista extendida de códigos de área móviles mexicanos
        mobile_area_codes = [
            '55', '33', '81', '22', '44', '66', '99', '77', '61', '64', '65',
            '67', '68', '69', '21', '24', '25', '26', '27', '28', '29', '31',
            '32', '34', '35', '36', '37', '38', '43', '45', '46', '47', '48',
            '49', '52', '53', '56', '58', '59', '62', '63', '71', '72', '73',
            '74', '75', '76', '78', '83', '84', '86', '87', '88', '89', '92',
            '93', '94', '95', '96', '97', '98'
        ]

        # Caso 1: Número de 12 dígitos sin el "1" (525528995412)
        if len(normalized) == 12:
            area_codes = normalized[2:4]
            # Log específico para debug del número real
            if normalized == '525559177302':
                print(
                    f"🔍 DEBUG: Procesando tu número específico {normalized}, código de área: {area_codes}")
            if area_codes in mobile_area_codes:
                # Insertar el "1" después del código de país para números móviles
                old_normalized = normalized
                normalized = '521' + normalized[2:]
                if old_normalized == '525559177302':
                    print(
                        f"🎯 DEBUG: Tu número transformado de {old_normalized} a {normalized}")

        # Caso 2: Número de 13 dígitos que ya tiene el "1" (5215528995412)
        elif len(normalized) == 13 and normalized[2] == '1':
            area_codes = normalized[3:5]
            if area_codes in mobile_area_codes:
                # Ya está en el formato correcto
                pass

        # Caso 3: Número de 13 dígitos pero sin el "1" en la posición correcta
        elif len(normalized) == 13 and normalized[2] != '1':
            area_codes = normalized[2:4]
            if area_codes in mobile_area_codes:
                # Reformatear: 52 + área + resto -> 521 + área + resto
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
