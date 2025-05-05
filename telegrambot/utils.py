# telegrambot/utils.py
from asgiref.sync import sync_to_async
from telegrambot.models import TelegramMessage
from langchain.schema import HumanMessage, AIMessage


async def fetch_last_messages(user_id: int, window: int = 10):
    def _query():
        return list(
            TelegramMessage.objects
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
