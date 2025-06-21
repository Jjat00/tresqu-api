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
    return name.strip().lower()


# FUNCIONES ACTUALIZADAS PARA CATEGORÍAS POR USUARIO

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
            return get_user_categories_with_details(user)
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
