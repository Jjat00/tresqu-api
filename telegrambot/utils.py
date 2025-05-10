from asgiref.sync import sync_to_async
from users.models import Message
from langchain.schema import HumanMessage, AIMessage
from categories.models import Category


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
