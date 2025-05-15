import logging
import json
import requests
import time
import urllib.parse
from django.db import transaction, connections, connection, InterfaceError
from django.conf import settings
from asgiref.sync import sync_to_async

from users.models import User, Chat, Message
from .services import process_message
from .utils import normalize_phone_number

# Configuración de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# Funciones síncronas para operaciones de base de datos
def get_or_create_chat(phone_number):
    """
    Obtiene o crea un chat con manejo de errores de conexión
    """
    max_retries = 3
    retry_count = 0
    backoff_time = 0.5  # tiempo inicial en segundos

    while retry_count < max_retries:
        try:
            with transaction.atomic():
                return Chat.objects.get_or_create(
                    platform='WHATSAPP',
                    platform_chat_id=str(phone_number),
                    defaults={'platform': 'WHATSAPP'}
                )
        except InterfaceError:
            # La conexión se cerró, intentar reconectar
            retry_count += 1
            logger.warning(
                f"Conexión cerrada, intento {retry_count} de reconexión para chat_id={phone_number}")

            # Con el pool de conexiones, no necesitamos cerrar manualmente
            # simplemente reintentamos después de un tiempo

            if retry_count < max_retries:
                # Esperar con backoff exponencial
                time.sleep(backoff_time)
                backoff_time *= 2
                continue
            else:
                logger.error(
                    f"No se pudo reconectar después de {max_retries} intentos")
                raise
        except Exception as e:
            logger.error(f"Error al obtener/crear chat: {e}")
            raise


def create_message(chat, message_id, message_type, text):
    """Crea un mensaje con reintentos en caso de conexión cerrada"""
    max_retries = 3
    retry_count = 0
    backoff_time = 0.5  # tiempo inicial en segundos

    # Generar embedding para mensajes no vacíos
    embedding = None
    if text and text.strip():
        try:
            # Usar servicio de embeddings si está disponible
            from telegrambot.tools import embeddings
            embedding = embeddings.embed_query(text)
        except Exception as e:
            logger.error(f"Error al generar embedding para mensaje: {e}")

    while retry_count < max_retries:
        try:
            with transaction.atomic():
                return Message.objects.create(
                    chat=chat,
                    platform_message_id=message_id,
                    message_type=message_type,
                    text=text,
                    embedding=embedding
                )
        except InterfaceError:
            # La conexión se cerró, intentar reconectar
            retry_count += 1
            logger.warning(
                f"Conexión cerrada, intento {retry_count} de reconexión para create_message")

            # Con el pool de conexiones, no necesitamos cerrar manualmente

            if retry_count < max_retries:
                # Esperar con backoff exponencial
                time.sleep(backoff_time)
                backoff_time *= 2
                continue
            else:
                logger.error(
                    f"No se pudo reconectar después de {max_retries} intentos")
                raise
        except Exception as e:
            logger.error(f"Error al crear mensaje: {e}")
            raise


def get_user_by_external_id(external_id):
    """Busca un usuario por su ID externo"""
    return User.objects.filter(external_id=external_id).first()


def get_user_by_phone_number(phone_number):
    """Busca un usuario por número de teléfono"""
    # Normalizar el número de teléfono (eliminar el signo + si existe)
    normalized_phone = normalize_phone_number(phone_number)
    return User.objects.filter(phone_number=normalized_phone).first()


def create_user(external_id, platform, first_name, username=None, phone_number=None, default_currency='USD'):
    """Crea un nuevo usuario"""
    # Normalizar el número de teléfono (eliminar el signo + si existe)
    normalized_phone = normalize_phone_number(phone_number)
    return User.objects.create(
        external_id=external_id,
        platform=platform,
        first_name=first_name or "",
        username=username or "",
        phone_number=normalized_phone,
        default_currency=default_currency
    )


def update_chat_user(chat, user):
    """Actualiza el usuario asociado a un chat"""
    chat.user = user
    chat.save()
    return chat


def update_user_currency(user, currency_code):
    """Actualiza la moneda por defecto del usuario"""
    user.default_currency = currency_code
    user.save()
    return user


def get_chat_user(phone_number):
    """Obtiene el usuario asociado a un chat por número de teléfono"""
    max_retries = 3
    retry_count = 0
    backoff_time = 0.5  # tiempo inicial en segundos

    while retry_count < max_retries:
        try:
            with transaction.atomic():
                chat = Chat.objects.get(
                    platform='WHATSAPP', platform_chat_id=str(phone_number))
                # Hacemos una consulta explícita en lugar de usar chat.user
                # para evitar problemas con el acceso lazy a relaciones
                return chat.user_id, User.objects.filter(id=chat.user_id).first() if chat.user_id else None
        except InterfaceError:
            # La conexión se cerró, intentar reconectar
            retry_count += 1
            logger.warning(
                f"Conexión cerrada, intento {retry_count} de reconexión para get_chat_user")

            # Con el pool de conexiones, no necesitamos cerrar manualmente

            if retry_count < max_retries:
                # Esperar con backoff exponencial
                time.sleep(backoff_time)
                backoff_time *= 2
                continue
            else:
                logger.error(
                    f"No se pudo reconectar después de {max_retries} intentos")
                raise
        except Chat.DoesNotExist:
            return None, None
        except Exception as e:
            logger.error(f"Error al obtener usuario de chat: {e}")
            raise


async def handle_whatsapp_message(sender_number, message_text, message_id, instance_name, server_url, api_key):
    """
    Procesa un mensaje entrante de WhatsApp y envía una respuesta
    """
    try:
        # 1. Obtener o crear el chat para este número
        chat, created = await sync_to_async(get_or_create_chat)(sender_number)

        # 2. Guardar el mensaje entrante
        await sync_to_async(create_message)(
            chat, message_id, "incoming", message_text
        )

        # 3. Obtener el usuario asociado al chat
        user_id, user = await sync_to_async(get_chat_user)(sender_number)

        # 4. Si no hay usuario, crear uno nuevo o buscarlo por número de teléfono
        if not user:
            # Intentar encontrar por número de teléfono primero
            user = await sync_to_async(get_user_by_phone_number)(sender_number)

            if not user:
                # Crear un nuevo usuario si no existe
                external_id = f"wa_{sender_number}"
                name = f"Usuario WhatsApp {sender_number[-4:]}"
                user = await sync_to_async(create_user)(
                    external_id=external_id,
                    platform="WHATSAPP",
                    first_name=name,
                    phone_number=sender_number
                )
                logger.info(f"Nuevo usuario creado: {user}")

            # Asociar el usuario con el chat
            await sync_to_async(update_chat_user)(chat, user)

        # 5. Procesar el mensaje y obtener respuesta
        response_text = await process_message(user, message_text)

        # 6. Guardar la respuesta en la base de datos
        await sync_to_async(create_message)(
            chat, f"response_{message_id}", "outgoing", response_text
        )

        # 7. Enviar la respuesta al usuario
        success = await sync_to_async(send_whatsapp_response)(
            instance_name=instance_name,
            to_number=sender_number,
            message=response_text,
            server_url=server_url,
            api_key=api_key
        )

        if success:
            logger.info(
                f"✅ Respuesta enviada a {sender_number}: {response_text[:30]}...")
        else:
            logger.error(f"❌ Error al enviar respuesta a {sender_number}")

        return success, response_text

    except Exception as e:
        logger.exception(f"Error procesando mensaje de WhatsApp: {e}")
        return False, f"Error: {str(e)}"


def send_whatsapp_response(instance_name, to_number, message, server_url=None, api_key=None):
    """
    Envía una respuesta a un número de WhatsApp utilizando la API

    Args:
        instance_name (str): Nombre de la instancia de WhatsApp
        to_number (str): Número de teléfono del destinatario
        message (str): Mensaje a enviar
        server_url (str): URL del servidor de WhatsApp
        api_key (str): Clave de API del servidor de WhatsApp
    """
    try:
        # Si no se proporcionan estos valores, usar los predeterminados de configuración
        if not server_url:
            server_url = getattr(
                settings, 'EVOLUTION_API_URL', 'http://localhost:8080')
        if not api_key:
            api_key = getattr(settings, 'GLOBAL_API_KEY', '')

        # Asegurarnos de que el número tiene formato internacional
        if not to_number.startswith('+'):
            # Asumimos que es un número sin el símbolo +
            to_number_formatted = to_number
        else:
            to_number_formatted = to_number[1:]  # Quitar el + si existe

        logger.info(f"Intentando enviar mensaje a {to_number_formatted}")

        # Construir la URL correcta para enviar mensajes - codificar el nombre de la instancia si contiene espacios
        import urllib.parse
        encoded_instance = urllib.parse.quote(instance_name)
        api_url = f"{server_url}/message/sendText/{encoded_instance}"

        # Preparar el payload según la documentación proporcionada
        payload = {
            "number": to_number_formatted,
            "text": message
        }

        # Log detallado del payload para depuración
        logger.info(f"URL de envío: {api_url}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")

        # Configurar headers con la API key
        headers = {
            "Content-Type": "application/json",
            "apikey": api_key
        }

        # Realizar la solicitud
        try:
            logger.info(f"Enviando mensaje a través de: {api_url}")
            response = requests.post(
                api_url, json=payload, headers=headers, timeout=10)
            logger.info(f"Respuesta: Código {response.status_code}")

            if response.status_code == 200 or response.status_code == 201:
                try:
                    response_data = response.json()
                    logger.info(
                        f"Respuesta: {json.dumps(response_data, indent=2)}")
                    return True
                except:
                    if "success" in response.text.lower():
                        logger.info("Mensaje enviado (respuesta no es JSON)")
                        return True
                    return True
            else:
                logger.error(
                    f"Error al enviar mensaje. Código: {response.status_code}, Respuesta: {response.text}")
                return False

        except Exception as e:
            logger.exception(f"Error al enviar mensaje: {str(e)}")
            return False

    except requests.RequestException as e:
        logger.exception(f"Error de conexión al enviar mensaje: {str(e)}")
        return False
    except Exception as e:
        logger.exception(f"Error inesperado al enviar mensaje: {str(e)}")
        return False
