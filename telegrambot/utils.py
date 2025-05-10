from asgiref.sync import sync_to_async
from users.models import Message
from langchain.schema import HumanMessage, AIMessage
from categories.models import Category
from income.models import IncomeCategory


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


@sync_to_async
def get_existing_categories():
    """
    Obtiene la lista de todas las categorías disponibles para gastos
    desde la base de datos.
    """
    try:
        categories = Category.get_all_categories()
        return categories
    except Exception as e:
        print(f"Error al obtener categorías: {e}")
        return []


@sync_to_async
def get_existing_income_categories():
    """
    Obtiene la lista de todas las categorías disponibles para ingresos
    desde la base de datos.
    """
    try:
        categories = IncomeCategory.get_all_categories()
        return categories
    except Exception as e:
        print(f"Error al obtener categorías de ingresos: {e}")
        return []


@sync_to_async
def get_categories_with_details():
    """
    Obtiene un diccionario con todas las categorías disponibles para gastos e ingresos
    incluyendo su descripción y ejemplos.

    Returns:
        dict: Diccionario donde cada clave es el nombre de la categoría y cada valor
              es otro diccionario con 'description', 'examples' y 'color'.
    """
    try:
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
