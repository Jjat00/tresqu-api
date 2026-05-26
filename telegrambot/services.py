# services.py — Telegram entry point.
#
# The heavy lifting (multi-agent supervisor, subagents, tools, prompts) lives
# in ``agents/``. This module keeps Telegram-specific concerns: audio
# transcription, history loading, and exposing the public ``process_message``
# / ``transcribe_audio`` API used by ``telegrambot/bot.py`` and views.

import asyncio
import logging

from django.conf import settings
from openai import OpenAI

from telegrambot.utils import fetch_last_messages
from telegrambot.config import (
    OPENAI_MAX_RETRIES,
    OPENAI_REQUEST_TIMEOUT,
    RETRY_BASE_DELAY,
    RETRY_MAX_DELAY,
    RETRYABLE_ERROR_PATTERNS,
)
from telegrambot.utils import log_ssl_error_details
from users.models import User

from agents.services import process_message as _agent_process_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Reused by ``telegrambot/views.py`` for ad-hoc Whisper calls.
openai_client = OpenAI(
    api_key=settings.OPENAI_API_KEY,
    timeout=float(OPENAI_REQUEST_TIMEOUT),
)


async def build_history(user_id: int) -> list:
    """Load the last LangChain messages for the given Telegram user."""
    messages = []
    async for msg in fetch_last_messages(user_id):
        messages.append(msg)
    return messages


async def transcribe_audio(audio_file_path: str) -> str:
    """Transcribe an audio file using OpenAI Whisper with retry on retryable errors."""

    def do_transcription():
        with open(audio_file_path, "rb") as audio_file:
            transcription = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )
            return transcription.text

    try:
        for attempt in range(3):
            try:
                return await asyncio.to_thread(do_transcription)
            except Exception as exc:
                error_str = str(exc).lower()
                is_retryable = any(p in error_str for p in RETRYABLE_ERROR_PATTERNS)
                if not is_retryable or attempt == 2:
                    raise
                delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                logger.warning(
                    f"Error en transcripción (intento {attempt + 1}/3): {exc}. "
                    f"Reintentando en {delay}s..."
                )
                await asyncio.sleep(delay)
        return ""
    except Exception as exc:
        log_ssl_error_details(exc, "Audio transcription")
        logger.error(f"Error transcribiendo audio después de reintentos: {exc}")
        return ""


async def process_message(user: User, raw_text: str) -> str:
    """Public Telegram entry point — delegates to the unified agents supervisor."""

    history = await build_history(user.id)
    response = await _agent_process_message(
        user=user,
        raw_text=raw_text,
        channel="telegram",
        history=history,
    )
    return response.text
