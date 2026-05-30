# services.py — WhatsApp entry point.
#
# Multi-agent orchestration (supervisor + subagents + tools + prompts) lives
# in ``agents/``. This module owns WhatsApp-specific concerns: audio
# transcription, image/media download, OCR of receipts, history loading, and
# the public ``process_message`` used by the webhook.

import asyncio
import logging
import tempfile

from django.conf import settings
from openai import OpenAI

from users.models import User
from whatsappbot.utils import fetch_last_messages

from agents.services import process_message as _agent_process_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)


async def transcribe_audio(audio_file_path: str) -> str:
    """Transcribe an audio file using OpenAI Whisper."""

    def do_transcription():
        with open(audio_file_path, "rb") as audio_file:
            transcription = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )
            return transcription.text

    try:
        return await asyncio.to_thread(do_transcription)
    except Exception as exc:
        logger.error(f"Error transcribiendo audio: {exc}")
        return ""


async def extract_expenses_from_image(image_url: str) -> str:
    """Extract expenses from an invoice/ticket image via GPT-4o vision."""

    extraction_prompt = """Analiza esta imagen y extrae ÚNICAMENTE los gastos o compras REALES que encuentres.

    REGLAS IMPORTANTES:
    1. NUNCA incluyas el TOTAL de la factura si ya extrajiste los items individuales
    2. NUNCA incluyas subtotales, impuestos separados, propinas, o valores informativos
    3. SOLO extrae los productos/servicios que el usuario realmente compró
    4. Si la factura tiene items desglosados, extrae SOLO esos items individuales (NO el total)
    5. SOLO usa el total cuando es un recibo simple SIN desglose de items

    Para cada gasto REAL, identifica:
    - Monto (cantidad numérica del producto/servicio)
    - Moneda: SOLO si está claramente visible en la imagen (símbolo o código). NUNCA la infieras
      ni asumas una moneda "local"; si no es clara, OMÍTELA por completo (no escribas ninguna
      moneda) — el sistema usará la moneda por defecto del usuario.
    - Descripción o concepto del gasto
    - Fecha (si está visible)

    Formato de respuesta:
    Para CADA gasto REAL escribe en una línea separada:
    "[Monto] [Moneda] en [Descripción/Concepto]"

    Si no encuentras gastos claros, di "No se encontraron gastos en la imagen".
    """

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": extraction_prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            temperature=0,
        )
        extracted_text = response.choices[0].message.content
        logger.info(f"Gastos extraídos de imagen: {extracted_text}")
        return extracted_text or ""
    except Exception as exc:
        logger.error(f"Error extrayendo gastos de imagen: {exc}")
        return ""


async def _download_whatsapp_file(media_id: str, access_token: str, mime_to_ext: dict, default_ext: str) -> str:
    """Internal helper used by ``download_whatsapp_image`` / ``download_whatsapp_media``."""

    import requests

    try:
        media_url_endpoint = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {access_token}"}

        meta = requests.get(media_url_endpoint, headers=headers, timeout=30)
        meta.raise_for_status()
        media_info = meta.json()

        file_url = media_info.get("url")
        mime_type = media_info.get("mime_type", "")
        if not file_url:
            logger.error("No se pudo obtener la URL del archivo de media")
            return ""

        binary = requests.get(file_url, headers=headers, timeout=60)
        binary.raise_for_status()

        extension = default_ext
        for mime_prefix, ext in mime_to_ext.items():
            if mime_prefix in mime_type:
                extension = ext
                break

        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
            tmp.write(binary.content)
            temp_path = tmp.name

        logger.info(f"Archivo de media descargado exitosamente: {temp_path}")
        return temp_path
    except Exception as exc:
        logger.error(f"Error descargando archivo de media de WhatsApp: {exc}")
        return ""


async def download_whatsapp_image(media_id: str, access_token: str) -> str:
    """Download an image attachment from WhatsApp and return the local temp path."""

    return await _download_whatsapp_file(
        media_id,
        access_token,
        mime_to_ext={
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        },
        default_ext=".jpg",
    )


async def download_whatsapp_media(media_id: str, access_token: str) -> str:
    """Download an audio attachment from WhatsApp and return the local temp path."""

    return await _download_whatsapp_file(
        media_id,
        access_token,
        mime_to_ext={
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
        },
        default_ext=".ogg",
    )


async def build_history(user_id: int) -> list:
    """Load the last LangChain messages for the given WhatsApp user."""

    messages = []
    async for msg in fetch_last_messages(user_id):
        messages.append(msg)
    return messages


async def process_message(user: User, raw_text: str, sender_phone: str | None = None) -> str:
    """Public WhatsApp entry point — delegates to the unified agents supervisor.

    If the agent produced a pending Wallbit confirmation, send the interactive
    confirmation buttons before returning the textual recap.
    """

    history = await build_history(user.id)
    response = await _agent_process_message(
        user=user,
        raw_text=raw_text,
        channel="whatsapp",
        history=history,
    )

    pending = response.pending_confirmation
    if pending and sender_phone:
        try:
            from .wallbit_handlers import send_confirmation_buttons

            send_confirmation_buttons(
                phone=sender_phone,
                decision_id=pending["confirmation_id"],
                preview=pending.get("preview", {}),
                two_step=pending.get("two_step_required", False),
            )
            return response.text or "Te envié la propuesta — confirma con el botón."
        except Exception as exc:
            logger.exception(f"send_confirmation_buttons failed: {exc}")

    return response.text
