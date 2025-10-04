import requests
from django.conf import settings
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Servicio para enviar mensajes y gestionar indicadores de WhatsApp usando Meta API v23.0"""

    def __init__(self):
        """
        Inicializa el servicio de WhatsApp

        Args:
            phone_number_id: ID del número de teléfono de WhatsApp Business
                           Si no se proporciona, se obtiene de settings
        """
        self.phone_number_id = getattr(
            settings, 'META_WHATSAPP_PHONE_NUMBER_ID', '')
        self.access_token = getattr(
            settings, 'META_WHATSAPP_ACCESS_TOKEN', '')
        self.base_url = f"https://graph.facebook.com/v23.0/{self.phone_number_id}/messages"
        self.headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

    def send_reaction(self, recipient_number: str, message_id: str, emoji: str = '⏳') -> Optional[Dict]:
        """
        Envía una reacción a un mensaje específico (útil como indicador visual)

        NOTA: La API de WhatsApp Business NO tiene un verdadero "typing indicator".
        Esta función envía una reacción emoji a un mensaje, que puede usarse para
        indicar que el bot está procesando (ej: ⏳, ⌛, 👀)

        Args:
            recipient_number: Número del destinatario (formato: 521234567890)
            message_id: ID del mensaje al que reaccionar
            emoji: Emoji a usar como reacción (⏳, ⌛, 💬, 👀, 🤖)

        Returns:
            Respuesta de la API o None si hay error
        """
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': recipient_number,
            'type': 'reaction',
            'reaction': {
                'message_id': message_id,
                'emoji': emoji
            }
        }

        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"✅ Reacción '{emoji}' enviada a {recipient_number}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f'⚠️ Error enviando reacción: {e}')
            return None

    def mark_as_read(self, message_id: str) -> Optional[Dict]:
        """
        Marca un mensaje como leído (muestra checks azules al usuario)

        Esta es la forma oficial de mostrar al usuario que su mensaje fue recibido
        y está siendo procesado por el bot.

        Args:
            message_id: ID del mensaje a marcar como leído

        Returns:
            Respuesta de la API o None si hay error
        """
        payload = {
            'messaging_product': 'whatsapp',
            'status': 'read',
            'message_id': message_id,
            "typing_indicator": {
                "type": "text"
            }
        }

        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"✅ Mensaje {message_id} marcado como leído")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f'⚠️ Error marcando como leído: {e}')
            return None

    def remove_reaction(self, recipient_number: str, message_id: str) -> Optional[Dict]:
        """
        Elimina una reacción previamente enviada

        Útil para remover el indicador de "procesando" una vez completada la tarea

        Args:
            recipient_number: Número del destinatario
            message_id: ID del mensaje del cual remover la reacción

        Returns:
            Respuesta de la API o None si hay error
        """
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': recipient_number,
            'type': 'reaction',
            'reaction': {
                'message_id': message_id,
                'emoji': ''  # Emoji vacío = remover reacción
            }
        }

        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"✅ Reacción removida de mensaje {message_id}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f'⚠️ Error removiendo reacción: {e}')
            return None
