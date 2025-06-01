from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.conf import settings
import json
import logging
import asyncio
import requests
import random
import string
import time
from django.core.cache import cache
from rest_framework_simplejwt.tokens import RefreshToken
from users.models import User, SubscriptionPlan
from users.serializers import UserSerializer

from .bot import handle_whatsapp_message
from .utils import normalize_phone_number

logger = logging.getLogger(__name__)

# Duración de validez del código de verificación (en segundos)
VERIFICATION_CODE_TIMEOUT = 300  # 5 minutos

# Prefijo para las claves en caché
VERIFICATION_CODE_PREFIX = 'whatsapp_verification_code_'

# Token de verificación para Meta WhatsApp API
META_VERIFY_TOKEN = getattr(
    settings, 'META_WHATSAPP_VERIFY_TOKEN', 'mi_token_secreto')


@csrf_exempt
@require_http_methods(["GET", "POST"])
def meta_webhook(request):
    """
    Endpoint para manejar el webhook de Meta WhatsApp API
    GET: Verificación del webhook
    POST: Recepción de eventos de WhatsApp
    """
    if request.method == 'GET':
        return verify_meta_webhook(request)
    elif request.method == 'POST':
        return handle_meta_webhook(request)


def verify_meta_webhook(request):
    """
    Verifica el webhook de Meta WhatsApp API
    Responde al challenge de verificación de Meta
    """
    try:
        # Obtener parámetros de la verificación
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        logger.info(
            f"Verificación de webhook Meta - Mode: {mode}, Token: {token}, Challenge: {challenge}")

        # Verificar que el modo sea 'subscribe' y el token coincida
        if mode == 'subscribe' and token == META_VERIFY_TOKEN:
            logger.info("✅ Verificación de webhook Meta exitosa")
            return HttpResponse(challenge, content_type='text/plain')
        else:
            logger.error(
                f"❌ Verificación de webhook Meta fallida - Token esperado: {META_VERIFY_TOKEN}")
            return HttpResponse('Forbidden', status=403)

    except Exception as e:
        logger.exception(f"Error en verificación de webhook Meta: {str(e)}")
        return HttpResponse('Error', status=500)


def handle_meta_webhook(request):
    """
    Procesa los eventos recibidos desde Meta WhatsApp API
    """
    try:
        # Parsear el JSON del webhook
        webhook_data = json.loads(request.body)
        logger.info(
            f"Evento recibido de Meta WhatsApp: {json.dumps(webhook_data, indent=2)}")

        # Verificar que hay entradas en el webhook
        entries = webhook_data.get('entry', [])
        if not entries:
            logger.warning("No se encontraron entradas en el webhook de Meta")
            return JsonResponse({"status": "success", "message": "Sin entradas para procesar"})

        # Procesar cada entrada
        for entry in entries:
            entry_id = entry.get('id')  # WHATSAPP_BUSINESS_ACCOUNT_ID
            changes = entry.get('changes', [])

            logger.info(
                f"Procesando entrada {entry_id} con {len(changes)} cambios")

            # Procesar cada cambio
            for change in changes:
                field = change.get('field')
                value = change.get('value', {})

                if field == 'messages':
                    # Procesar mensajes entrantes
                    process_meta_messages(value, entry_id)
                elif field == 'message_status':
                    # Procesar actualizaciones de estado de mensajes
                    process_meta_message_status(value, entry_id)
                else:
                    logger.info(f"Campo no manejado: {field}")

        return JsonResponse({"status": "success", "message": "Evento procesado correctamente"})

    except json.JSONDecodeError:
        logger.error("Error al decodificar JSON del webhook de Meta")
        return JsonResponse({"status": "error", "message": "JSON inválido"}, status=400)
    except Exception as e:
        logger.exception(f"Error al procesar webhook de Meta: {str(e)}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


def process_meta_messages(value, waba_id):
    """
    Procesa los mensajes entrantes de Meta WhatsApp API
    """
    try:
        messages = value.get('messages', [])
        contacts = value.get('contacts', [])

        # Crear un diccionario de contactos para fácil acceso
        contacts_dict = {}
        for contact in contacts:
            wa_id = contact.get('wa_id')
            profile = contact.get('profile', {})
            contacts_dict[wa_id] = {
                'name': profile.get('name', ''),
                'wa_id': wa_id
            }

        # Procesar cada mensaje
        for message in messages:
            try:
                # Información básica del mensaje
                message_id = message.get('id')
                from_number = message.get('from')
                timestamp = message.get('timestamp')
                message_type = message.get('type', 'text')

                # Obtener información del contacto
                contact_info = contacts_dict.get(from_number, {})
                sender_name = contact_info.get('name', '')

                logger.info(
                    f"Procesando mensaje Meta - ID: {message_id}, De: {from_number}, Tipo: {message_type}")

                # Extraer contenido del mensaje según el tipo
                message_text = ""
                media_url = None

                if message_type == 'text':
                    message_text = message.get('text', {}).get('body', '')
                elif message_type == 'image':
                    image_data = message.get('image', {})
                    message_text = image_data.get('caption', '')
                    media_url = image_data.get('id')  # Media ID para descargar
                elif message_type == 'audio':
                    audio_data = message.get('audio', {})
                    media_url = audio_data.get('id')
                elif message_type == 'video':
                    video_data = message.get('video', {})
                    message_text = video_data.get('caption', '')
                    media_url = video_data.get('id')
                elif message_type == 'document':
                    document_data = message.get('document', {})
                    message_text = document_data.get('caption', '')
                    media_url = document_data.get('id')
                elif message_type == 'voice':
                    voice_data = message.get('voice', {})
                    media_url = voice_data.get('id')
                else:
                    logger.info(
                        f"Tipo de mensaje no soportado: {message_type}")
                    continue

                # Procesar el mensaje usando la lógica existente
                # Nota: Necesitaremos adaptar handle_whatsapp_message para Meta API
                success, response = asyncio.run(handle_meta_whatsapp_message(
                    sender_number=from_number,
                    message_text=message_text,
                    message_id=message_id,
                    waba_id=waba_id,
                    sender_name=sender_name,
                    message_type=message_type,
                    media_url=media_url
                ))

                if success:
                    logger.info(
                        f"✅ Mensaje Meta procesado exitosamente para {from_number}")
                else:
                    logger.error(
                        f"❌ Error procesando mensaje Meta para {from_number}")

            except Exception as e:
                logger.exception(
                    f"Error procesando mensaje individual de Meta: {str(e)}")
                continue

    except Exception as e:
        logger.exception(f"Error procesando mensajes de Meta: {str(e)}")


def process_meta_message_status(value, waba_id):
    """
    Procesa las actualizaciones de estado de mensajes de Meta WhatsApp API
    """
    try:
        statuses = value.get('statuses', [])

        for status in statuses:
            message_id = status.get('id')
            status_type = status.get('status')  # sent, delivered, read, failed
            timestamp = status.get('timestamp')
            recipient_id = status.get('recipient_id')

            # Manejar errores específicos
            errors = status.get('errors', [])

            if status_type == 'failed' and errors:
                for error in errors:
                    error_code = error.get('code')
                    error_title = error.get('title', '')
                    error_message = error.get('message', '')
                    error_details = error.get(
                        'error_data', {}).get('details', '')

                    logger.error(
                        f"❌ Error en mensaje Meta - ID: {message_id}, "
                        f"Para: {recipient_id}, Código: {error_code}, "
                        f"Título: {error_title}, Detalles: {error_details}"
                    )

                    # Manejar errores específicos
                    if error_code == 131047:
                        logger.warning(
                            f"🔄 Error 131047 (Re-engagement) para {recipient_id}: "
                            f"Se requiere plantilla de mensaje para contactar después de 24h"
                        )
                    elif error_code == 131026:
                        logger.warning(
                            f"📱 Error 131026 (Message Undeliverable) para {recipient_id}: "
                            f"Número no válido o usuario no acepta términos"
                        )
                    elif error_code == 131048:
                        logger.warning(
                            f"🚫 Error 131048 (Spam Rate Limit) para {recipient_id}: "
                            f"Demasiados mensajes marcados como spam"
                        )
                    elif error_code == 131049:
                        logger.warning(
                            f"🌐 Error 131049 (Ecosystem Health) para {recipient_id}: "
                            f"Meta no entregó para mantener ecosistema saludable"
                        )
                    elif error_code == 131042:
                        logger.error(
                            f"💳 Error 131042 (Payment Issue) para {recipient_id}: "
                            f"Problema con método de pago de la cuenta"
                        )
                    elif error_code in [130429, 131056, 80007]:
                        logger.warning(
                            f"⏱️ Error {error_code} (Rate Limit) para {recipient_id}: "
                            f"Límite de velocidad alcanzado"
                        )
                    else:
                        logger.error(
                            f"❓ Error desconocido {error_code} para {recipient_id}: {error_message}"
                        )
            else:
                logger.info(
                    f"📊 Estado de mensaje Meta - ID: {message_id}, "
                    f"Estado: {status_type}, Para: {recipient_id}"
                )

    except Exception as e:
        logger.exception(
            f"Error procesando estados de mensajes de Meta: {str(e)}")


async def handle_meta_whatsapp_message(sender_number, message_text, message_id, waba_id, sender_name="", message_type="text", media_url=None):
    """
    Maneja mensajes de WhatsApp usando Meta API
    Adaptación de la función existente para Meta API
    """
    try:
        # Por ahora, usar la lógica existente adaptada para Meta
        # Necesitarás implementar el envío de respuestas usando Meta API

        # Usar la función existente como base
        success, response = await handle_whatsapp_message(
            sender_number=sender_number,
            message_text=message_text,
            message_id=message_id,
            instance_name="meta_api",  # Identificador especial para Meta API
            server_url="https://graph.facebook.com",  # URL base de Meta API
            api_key=getattr(settings, 'META_WHATSAPP_ACCESS_TOKEN', ''),
            sender_name=sender_name,
            message_type=message_type,
            media_url=media_url
        )

        return success, response

    except Exception as e:
        logger.exception(f"Error manejando mensaje de Meta WhatsApp: {str(e)}")
        return False, str(e)


def send_meta_whatsapp_message(phone_number, message_text, waba_id=None, use_template=False, template_name=None, template_language="es", template_params=None):
    """
    Envía un mensaje usando Meta WhatsApp API
    Soporta tanto mensajes de texto como plantillas
    """
    try:
        # Configuración de Meta API
        access_token = getattr(settings, 'META_WHATSAPP_ACCESS_TOKEN', '')
        phone_number_id = getattr(
            settings, 'META_WHATSAPP_PHONE_NUMBER_ID', '')

        if not access_token or not phone_number_id:
            logger.error("Configuración de Meta WhatsApp API incompleta")
            return False

        # URL de la API de Meta
        url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"

        # Headers
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        # Construir payload según el tipo de mensaje
        if use_template and template_name:
            # Usar plantilla de mensaje
            payload = {
                "messaging_product": "whatsapp",
                "to": phone_number,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {
                        "code": template_language
                    }
                }
            }

            # Agregar parámetros si se proporcionan
            if template_params:
                payload["template"]["components"] = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": param} for param in template_params
                        ]
                    }
                ]

            logger.info(
                f"Enviando plantilla Meta '{template_name}' a {phone_number}")
        else:
            # Mensaje de texto normal
            payload = {
                "messaging_product": "whatsapp",
                "to": phone_number,
                "type": "text",
                "text": {
                    "body": message_text
                }
            }

            logger.info(
                f"Enviando mensaje Meta a {phone_number}: {message_text}")

        # Realizar la petición
        response = requests.post(url, headers=headers,
                                 json=payload, timeout=30)

        if response.status_code == 200:
            response_data = response.json()
            logger.info(
                f"✅ Mensaje Meta enviado exitosamente: {response_data}")
            return True
        else:
            logger.error(
                f"❌ Error enviando mensaje Meta: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        logger.exception(f"Error enviando mensaje Meta WhatsApp: {str(e)}")
        return False


@csrf_exempt
@require_POST
def webhook_receiver(request, instance_name):
    """
    Recibe eventos de webhook de WhatsApp y procesa los mensajes entrantes
    """
    logger.info(
        f"Recibiendo evento de webhook para la instancia: {instance_name}")

    # Si la instancia es 'jaime', usar 'Tresqu Test' que sabemos que funciona
    if instance_name.lower() == 'jaime':
        instance_name = 'Tresqu Test'
        logger.info(f"Usando instancia conocida: {instance_name}")

    # Intentar parsear JSON
    try:
        webhook_data = json.loads(request.body)
    except json.JSONDecodeError:
        logger.error("Error al decodificar JSON del webhook")
        return JsonResponse(
            {"status": "error", "message": "JSON inválido"}, status=400
        )

    logger.info(f"Datos del webhook recibidos para: {instance_name}")

    # Procesar el evento según su tipo
    event_type = webhook_data.get('event')
    if event_type == 'messages.upsert':
        try:
            # Extraer información crítica del webhook
            data = webhook_data.get('data', {})
            server_url = webhook_data.get('server_url',
                                          getattr(settings, 'EVOLUTION_API_URL', 'http://localhost:8080'))
            api_key = webhook_data.get('apikey',
                                       getattr(settings, 'GLOBAL_API_KEY', ''))

            # Verificar si es un mensaje entrante (no enviado por nosotros)
            from_me = data.get('key', {}).get('fromMe', False)
            if from_me:
                logger.info("Ignorando mensaje propio")
                return JsonResponse({"status": "success", "message": "Mensaje propio ignorado"})

            # Obtener datos del mensaje
            remote_jid = data.get('key', {}).get('remoteJid', '')
            message_id = data.get('key', {}).get('id', '')
            message_obj = data.get('message', {})

            # Determinar el tipo de mensaje
            message_type = "text"  # Tipo por defecto
            conversation = ""
            media_url = None

            # Verificar si hay texto en el mensaje
            if 'conversation' in message_obj and message_obj.get('conversation'):
                conversation = message_obj.get('conversation', '')
            # Verificar si es un mensaje extendido con texto
            elif 'extendedTextMessage' in message_obj and message_obj.get('extendedTextMessage', {}).get('text'):
                conversation = message_obj.get(
                    'extendedTextMessage', {}).get('text', '')

            # Verificar si hay multimedia
            # Imagen
            if 'imageMessage' in message_obj:
                message_type = "image"
                caption = message_obj.get(
                    'imageMessage', {}).get('caption', '')
                if caption:
                    conversation = caption
                media_url = message_obj.get(
                    'imageMessage', {}).get('url', None)
                logger.info(
                    f"Mensaje de imagen recibido con caption: {caption}")

            # Audio
            elif 'audioMessage' in message_obj:
                message_type = "audio"
                media_url = message_obj.get(
                    'audioMessage', {}).get('url', None)
                logger.info("Mensaje de audio recibido")

            # PTT (Push to Talk / Nota de voz)
            elif 'pttMessage' in message_obj:
                message_type = "ptt"
                media_url = message_obj.get('pttMessage', {}).get('url', None)
                logger.info("Nota de voz recibida")

            # Video
            elif 'videoMessage' in message_obj:
                message_type = "video"
                caption = message_obj.get(
                    'videoMessage', {}).get('caption', '')
                if caption:
                    conversation = caption
                media_url = message_obj.get(
                    'videoMessage', {}).get('url', None)
                logger.info(
                    f"Mensaje de video recibido con caption: {caption}")

            # Documento
            elif 'documentMessage' in message_obj:
                message_type = "document"
                caption = message_obj.get(
                    'documentMessage', {}).get('caption', '')
                if caption:
                    conversation = caption
                media_url = message_obj.get(
                    'documentMessage', {}).get('url', None)
                logger.info(f"Documento recibido con caption: {caption}")

            # Extraer el nombre del remitente si está disponible en los metadatos
            # El nombre puede estar en diferentes ubicaciones dependiendo de la estructura del webhook
            sender_name = ''

            # Buscar en varias ubicaciones posibles
            if 'pushName' in data:
                sender_name = data.get('pushName', '')
            elif 'key' in data and 'pushName' in data.get('key', {}):
                sender_name = data.get('key', {}).get('pushName', '')
            elif 'participant' in data:
                # Algunos webhooks proporcionan participant en lugar de pushName
                sender_name = data.get('participant', {}).get('name', '')

            # Si no se encuentra en ninguna parte, intentar buscar en toda la estructura
            if not sender_name and webhook_data.get('pushName'):
                sender_name = webhook_data.get('pushName')

            logger.info(f"Nombre del remitente encontrado: {sender_name}")

            # Extraer el número del remitente (eliminar @s.whatsapp.net o @g.us si está presente)
            sender_number = remote_jid.split(
                '@')[0] if '@' in remote_jid else remote_jid

            # Si no hay número, ignoramos
            if not sender_number:
                logger.warning("Mensaje sin número de remitente válido")
                return JsonResponse({"status": "success", "message": "Evento procesado (sin remitente)"})

            # Log del mensaje recibido
            logger.info(
                f"Mensaje recibido de {sender_number} ({sender_name}): Tipo {message_type}, Texto: {conversation}")

            # Usar handle_whatsapp_message para procesar el mensaje y enviar respuesta
            success, response = asyncio.run(handle_whatsapp_message(
                sender_number=sender_number,
                message_text=conversation,
                message_id=message_id,
                instance_name=instance_name,
                server_url=server_url,
                api_key=api_key,
                sender_name=sender_name,
                message_type=message_type,
                media_url=media_url
            ))

            if success:
                logger.info(f"✅ Procesamiento completo para {sender_number}")
            else:
                logger.error(f"❌ Error en procesamiento para {sender_number}")

        except Exception as e:
            logger.exception(f"Error al procesar mensaje: {str(e)}")

    # Responder siempre con éxito
    return JsonResponse(
        {"status": "success", "message": "Evento recibido correctamente"},
        status=200
    )


def process_message_upsert(data, instance_name):
    """
    Procesa los mensajes entrantes
    """
    # Implementa lógica para procesar mensajes entrantes
    logger.info(f"Procesando mensaje entrante para {instance_name}")
    # Ejemplo: extraer el mensaje y hacer algo con él
    try:
        message = data.get('messages', [])[0] if data.get('messages') else None
        if message:
            logger.info(f"Mensaje recibido: {message}")
            # Aquí puedes agregar tu lógica de procesamiento
    except Exception as e:
        logger.exception(f"Error al procesar mensaje: {str(e)}")


def process_connection_update(data, instance_name):
    """
    Procesa las actualizaciones de conexión
    """
    # Implementa lógica para procesar actualizaciones de conexión
    logger.info(f"Procesando actualización de conexión para {instance_name}")
    try:
        connection_state = data.get('state')
        if connection_state:
            logger.info(f"Estado de conexión: {connection_state}")
            # Aquí puedes agregar tu lógica según el estado de conexión
    except Exception as e:
        logger.exception(
            f"Error al procesar actualización de conexión: {str(e)}")


# Función para enviar respuesta vía WhatsApp API
def send_whatsapp_response(instance_name, to_number, message, server_url=None, api_key=None, sender=None):
    """
    Envía una respuesta a un número de WhatsApp utilizando la API

    Args:
        instance_name (str): Nombre de la instancia de WhatsApp
        to_number (str): Número de teléfono del destinatario
        message (str): Mensaje a enviar
        server_url (str): URL del servidor de WhatsApp
        api_key (str): Clave de API del servidor de WhatsApp
        sender (str): Remitente del mensaje
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

        # Construir la URL correcta para enviar mensajes
        api_url = f"{server_url}/message/sendText/{instance_name}"

        # Preparar el payload según la documentación proporcionada
        payload = {
            "number": to_number_formatted,
            "text": message
        }

        # Log detallado del payload para depuración
        logger.info(f"URL de envío: {api_url}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")

        # Configurar headers con la API key - Evolution API usa apikey en lugar de Authorization
        headers = {
            "Content-Type": "application/json",
            "apikey": api_key
        }

        # Realizar la solicitud
        try:
            logger.info(f"Enviando mensaje a través de: {api_url}")
            response = requests.post(
                api_url, json=payload, headers=headers, timeout=60)
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


def generate_verification_code():
    """
    Genera un código de verificación numérico de 6 dígitos
    """
    return ''.join(random.choices(string.digits, k=6))


@csrf_exempt
@require_POST
def send_verification_code(request, instance_name):
    """
    Genera y envía un código de verificación al número de WhatsApp proporcionado
    """
    try:
        # Obtener datos de la solicitud
        data = json.loads(request.body) if request.body else {}

        # Verificar que se proporcionó un número de teléfono
        phone_number = data.get('phone_number')
        if not phone_number:
            return JsonResponse({
                "status": "error",
                "message": "Se requiere un número de teléfono"
            }, status=400)

        # Normalizar el número de teléfono para consistencia
        phone_number_normalized = normalize_phone_number(phone_number)
        if not phone_number_normalized:
            return JsonResponse({
                "status": "error",
                "message": "Número de teléfono inválido"
            }, status=400)

        logger.info(
            f"Número original: {phone_number}, Normalizado: {phone_number_normalized}")

        # Obtener la URL del servidor y API key (opcional)
        server_url = data.get('server_url', getattr(
            settings, 'EVOLUTION_API_URL', 'http://localhost:8080'))
        api_key = data.get('apikey', getattr(settings, 'GLOBAL_API_KEY', ''))

        # Generar un código de verificación
        verification_code = generate_verification_code()

        # Almacenar el código en caché con el número normalizado
        cache_key = f"{VERIFICATION_CODE_PREFIX}{phone_number_normalized}"
        cache.set(cache_key, verification_code, VERIFICATION_CODE_TIMEOUT)

        # Preparar el mensaje con el código
        message = f"Tu código de verificación para Tresqu es: {verification_code}\n\nEste código expirará en 5 minutos."

        # Enviar el código por WhatsApp usando el número original (para el envío)
        success = send_whatsapp_response(
            instance_name=instance_name,
            to_number=phone_number,  # Usar el número original para el envío
            message=message,
            server_url=server_url,
            api_key=api_key
        )

        if success:
            return JsonResponse({
                "status": "ok",
                "message": "Código de verificación enviado con éxito",
                "expires_in": VERIFICATION_CODE_TIMEOUT
            })
        else:
            # Si falla el envío, eliminar el código de la caché
            cache.delete(cache_key)
            logger.error(f"Fallo al enviar código a {phone_number_normalized}")
            return JsonResponse({
                "status": "error",
                "message": "No se pudo enviar el código de verificación"
            }, status=500)

    except Exception as e:
        logger.exception(f"Error al enviar código de verificación: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)


@csrf_exempt
@require_POST
def verify_code(request):
    """
    Verifica si el código proporcionado coincide con el almacenado para el número
    y genera tokens JWT si la verificación es exitosa
    """
    try:
        # Obtener datos de la solicitud
        data = json.loads(request.body) if request.body else {}

        # Verificar que se proporcionaron los campos necesarios
        phone_number = data.get('phone_number')
        code = data.get('code')

        if not phone_number or not code:
            return JsonResponse({
                "status": "error",
                "message": "Se requiere número de teléfono y código de verificación"
            }, status=400)

        # Normalizar el número de teléfono para consistencia
        phone_number_normalized = normalize_phone_number(phone_number)
        if not phone_number_normalized:
            return JsonResponse({
                "status": "error",
                "message": "Número de teléfono inválido"
            }, status=400)

        logger.info(
            f"Verificando código - Número original: {phone_number}, Normalizado: {phone_number_normalized}")

        # Obtener el código almacenado en caché usando el número normalizado
        cache_key = f"{VERIFICATION_CODE_PREFIX}{phone_number_normalized}"
        stored_code = cache.get(cache_key)

        if not stored_code:
            return JsonResponse({
                "status": "error",
                "message": "El código ha expirado o no existe"
            }, status=400)

        # Verificar si el código coincide
        if code == stored_code:
            # Eliminar el código usado
            cache.delete(cache_key)

            # Usar el número normalizado para todas las operaciones de base de datos
            phone_number_clean = phone_number_normalized

            # Construir external_id para WhatsApp
            whatsapp_external_id = f"wa_{phone_number_clean}"

            # Variables para seguimiento
            user = None
            user_action = "login"

            try:
                # Primero intentar buscar por external_id específico de WhatsApp
                user = User.objects.get(external_id=whatsapp_external_id)
                logger.info(
                    f"Usuario encontrado por external_id de WhatsApp: {whatsapp_external_id}")
            except User.DoesNotExist:
                logger.info(
                    f"No se encontró usuario con external_id: {whatsapp_external_id}")
                # Si no existe, buscar por número de teléfono (puede existir en Telegram)
                try:
                    user = User.objects.get(phone_number=phone_number_clean)
                    logger.info(
                        f"Usuario encontrado por número de teléfono: {phone_number_clean}")

                    # Actualizar external_id para incluir WhatsApp
                    # Si es de Telegram, tendrá formato tg_XXXXX, queremos preservar esto y añadir wa_XXXXX
                    if not user.external_id.startswith("wa_"):
                        # Guardar el external_id actual (podría ser de Telegram)
                        old_external_id = user.external_id

                        # Usar un separador para múltiples plataformas
                        if "," in user.external_id:
                            # Ya tiene múltiples plataformas
                            if whatsapp_external_id not in user.external_id:
                                # Añadir solo si no existe ya
                                user.external_id = f"{user.external_id},{whatsapp_external_id}"
                        else:
                            # Primera combinación de plataformas
                            user.external_id = f"{old_external_id},{whatsapp_external_id}"

                        # Si la plataforma era otra, actualizar a multimodo
                        if user.platform != "WHATSAPP":
                            user.platform = "MULTIPLTAFORMA"

                        user.save()
                        logger.info(
                            f"Usuario actualizado con external_id combinado: {user.external_id}")

                except User.DoesNotExist:
                    logger.info(
                        f"No se encontró usuario con número de teléfono: {phone_number_clean}")
                    # No existe un usuario con este número, crear uno nuevo
                    default_plan = SubscriptionPlan.objects.get(id=1)
                    user_name = data.get(
                        'name', f"Usuario de WhatsApp {phone_number_clean}")

                    user = User.objects.create(
                        external_id=whatsapp_external_id,
                        platform="WHATSAPP",
                        first_name=user_name,
                        phone_number=phone_number_clean,
                        subscription_plan=default_plan,
                        default_currency="COP",  # Moneda predeterminada
                        timezone="America/Bogota"  # Zona horaria predeterminada
                    )
                    user_action = "register"
                    logger.info(
                        f"Nuevo usuario creado con external_id: {whatsapp_external_id}")

            # Generar tokens JWT
            refresh = RefreshToken.for_user(user)

            # Serializar el usuario para incluirlo en la respuesta
            user_data = UserSerializer(user).data

            return JsonResponse({
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": user_data,
                "user_action": user_action,
                "message": "Inicio de sesión exitoso"
            })
        else:
            return JsonResponse({
                "status": "error",
                "message": "Código de verificación incorrecto"
            }, status=400)

    except Exception as e:
        logger.exception(f"Error al verificar código: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)


@csrf_exempt
@require_POST
def send_verification_code_meta(request):
    """
    Genera y envía un código de verificación al número de WhatsApp usando Meta API
    """
    try:
        # Obtener datos de la solicitud
        data = json.loads(request.body) if request.body else {}

        # Verificar que se proporcionó un número de teléfono
        phone_number = data.get('phone_number')
        if not phone_number:
            return JsonResponse({
                "status": "error",
                "message": "Se requiere un número de teléfono"
            }, status=400)

        # Normalizar el número de teléfono para consistencia
        phone_number_normalized = normalize_phone_number(phone_number)
        if not phone_number_normalized:
            return JsonResponse({
                "status": "error",
                "message": "Número de teléfono inválido"
            }, status=400)

        logger.info(
            f"Número original: {phone_number}, Normalizado: {phone_number_normalized}")

        # Generar un código de verificación
        verification_code = generate_verification_code()

        # Almacenar el código en caché con el número normalizado
        cache_key = f"{VERIFICATION_CODE_PREFIX}{phone_number_normalized}"
        cache.set(cache_key, verification_code, VERIFICATION_CODE_TIMEOUT)

        # Preparar el mensaje con el código
        message = f"Tu código de verificación para Tresqu es: {verification_code}\n\nEste código expirará en 5 minutos."

        # Enviar el código por WhatsApp usando Meta API
        success = send_meta_whatsapp_message(phone_number, message)

        if success:
            return JsonResponse({
                "status": "ok",
                "message": "Código de verificación enviado con éxito",
                "expires_in": VERIFICATION_CODE_TIMEOUT
            })
        else:
            # Si falla el envío, eliminar el código de la caché
            cache.delete(cache_key)
            logger.error(f"Fallo al enviar código a {phone_number_normalized}")
            return JsonResponse({
                "status": "error",
                "message": "No se pudo enviar el código de verificación"
            }, status=500)

    except Exception as e:
        logger.exception(f"Error al enviar código de verificación: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)


@csrf_exempt
@require_POST
def send_mass_message_api(request):
    """
    API endpoint para enviar mensajes masivos desde el dashboard web
    """
    try:
        # Verificar autenticación (puedes agregar tu lógica de autenticación aquí)
        # Por ahora, verificamos que tenga una API key válida
        api_key = request.headers.get(
            'Authorization', '').replace('Bearer ', '')
        if not api_key or api_key != getattr(settings, 'ADMIN_API_KEY', 'admin_secret_key'):
            return JsonResponse({
                "status": "error",
                "message": "No autorizado"
            }, status=401)

        # Obtener datos de la solicitud
        data = json.loads(request.body) if request.body else {}

        message = data.get('message')
        platform = data.get('platform', 'WHATSAPP')
        template = data.get('template')
        # Nombre de plantilla de Meta
        template_name = data.get('template_name')
        template_language = data.get('template_language', 'es')
        template_params = data.get('template_params', [])
        use_template = data.get('use_template', False)
        dry_run = data.get('dry_run', False)

        if not message and not template and not template_name:
            return JsonResponse({
                "status": "error",
                "message": "Se requiere un mensaje, template o template_name"
            }, status=400)

        # Usar template si se especifica
        if template:
            message = get_template_message(template)
        elif template_name:
            use_template = True

        # Obtener usuarios según la plataforma
        if platform == 'WHATSAPP':
            users = User.objects.filter(
                platform__in=['WHATSAPP', 'MULTIPLTAFORMA'],
                phone_number__isnull=False
            ).exclude(phone_number='')
        else:
            users = User.objects.filter(
                phone_number__isnull=False
            ).exclude(phone_number='')

        total_users = users.count()

        if total_users == 0:
            return JsonResponse({
                "status": "error",
                "message": "No se encontraron usuarios para enviar mensajes"
            }, status=400)

        if dry_run:
            # Solo retornar información sin enviar
            users_preview = []
            for user in users[:10]:
                users_preview.append({
                    'name': user.first_name,
                    'phone': user.phone_number,
                    'platform': user.platform
                })

            return JsonResponse({
                "status": "success",
                "message": "Vista previa generada",
                "total_users": total_users,
                "users_preview": users_preview,
                "message_preview": message if not use_template else f"Plantilla: {template_name}",
                "use_template": use_template,
                "template_name": template_name
            })

        # Enviar mensajes en segundo plano
        import threading
        thread = threading.Thread(
            target=send_mass_messages_background,
            args=(users, message, use_template, template_name,
                  template_language, template_params)
        )
        thread.start()

        return JsonResponse({
            "status": "success",
            "message": f"Envío masivo iniciado para {total_users} usuarios",
            "total_users": total_users,
            "use_template": use_template,
            "template_name": template_name if use_template else None
        })

    except Exception as e:
        logger.exception(f"Error en API de mensaje masivo: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)


def get_template_message(template):
    """Retorna mensajes predefinidos según el template"""
    templates = {
        'reminder': (
            "🔔 *Recordatorio de Tresqu*\n\n"
            "¡Hola! 👋 Esperamos que tengas un excelente día.\n\n"
            "💰 Recuerda registrar tus gastos e ingresos de hoy para mantener "
            "un control perfecto de tus finanzas.\n\n"
            "Simplemente envíame un mensaje como:\n"
            "• \"Gasté 25000 en almuerzo\"\n"
            "• \"Compré café por 5000\"\n"
            "• \"Gané 50000 en mi negocio\"\n\n"
            "📊 Revisa tu dashboard en https://tresqu.com\n\n"
            "¡Tus finanzas bajo control! 💪"
        ),
        'welcome': (
            "🎉 *¡Bienvenido a Tresqu!*\n\n"
            "Gracias por unirte a nuestra comunidad de control financiero. "
            "Estamos aquí para ayudarte a gestionar tus gastos e ingresos de manera inteligente.\n\n"
            "💡 Puedes empezar registrando tus movimientos enviándome mensajes como:\n"
            "• \"Gasté 30000 en supermercado\"\n"
            "• \"Recibí 100000 de mi trabajo\"\n\n"
            "¡Comencemos este viaje financiero juntos! 🚀"
        ),
        'daily_summary': (
            "📊 *Resumen Diario de Tresqu*\n\n"
            "¡Hola {name}! 👋\n\n"
            "Es un buen momento para revisar tus finanzas del día:\n\n"
            "💰 ¿Registraste todos tus gastos de hoy?\n"
            "💵 ¿Anotaste tus ingresos?\n\n"
            "📱 Revisa tu dashboard: https://tresqu.com\n\n"
            "¡Mantén el control de tus finanzas! 🎯"
        ),
        'weekly_reminder': (
            "📅 *Recordatorio Semanal de Tresqu*\n\n"
            "¡Hola {name}! 👋\n\n"
            "Es momento de revisar tu semana financiera:\n\n"
            "📈 ¿Cómo van tus gastos esta semana?\n"
            "💡 ¿Hay algún patrón que notes?\n"
            "🎯 ¿Estás cumpliendo tus metas?\n\n"
            "📊 Revisa tu resumen semanal: https://tresqu.com\n\n"
            "¡Sigue así! 💪"
        )
    }
    return templates.get(template, "")


def send_mass_messages_background(users, message, use_template=False, template_name=None, template_language="es", template_params=None):
    """Envía mensajes masivos en segundo plano"""
    sent_count = 0
    failed_count = 0
    re_engagement_errors = 0
    other_errors = 0
    total_users = users.count()

    logger.info(f"Iniciando envío masivo a {total_users} usuarios")
    if use_template:
        logger.info(
            f"Usando plantilla: {template_name} (idioma: {template_language})")
    else:
        logger.info(f"Usando mensaje de texto: {message[:50]}...")

    for i, user in enumerate(users, 1):
        try:
            # Personalizar mensaje con el nombre del usuario si no es plantilla
            if use_template:
                # Para plantillas, usar parámetros si se proporcionan
                user_template_params = template_params.copy() if template_params else []
                if user.first_name and "{name}" in str(user_template_params):
                    # Reemplazar {name} en los parámetros
                    user_template_params = [param.replace(
                        "{name}", user.first_name) for param in user_template_params]

                success = send_meta_whatsapp_message(
                    phone_number=user.phone_number,
                    message_text="",  # No se usa para plantillas
                    use_template=True,
                    template_name=template_name,
                    template_language=template_language,
                    template_params=user_template_params if user_template_params else None
                )
            else:
                # Mensaje de texto personalizado
                personalized_message = message.replace(
                    "{name}", user.first_name or "")

                success = send_meta_whatsapp_message(
                    phone_number=user.phone_number,
                    message_text=personalized_message
                )

            if success:
                sent_count += 1
                logger.info(
                    f"✅ [{i}/{total_users}] Mensaje enviado a {user.first_name} ({user.phone_number})")
            else:
                failed_count += 1
                logger.error(
                    f"❌ [{i}/{total_users}] Falló envío a {user.first_name} ({user.phone_number})")

            # Delay entre mensajes para evitar rate limiting
            if i < total_users:
                time.sleep(2)  # 2 segundos entre mensajes

        except Exception as e:
            failed_count += 1
            logger.error(
                f"❌ [{i}/{total_users}] Error enviando a {user.first_name}: {str(e)}")
            continue

    # Log del resumen final
    success_rate = (sent_count / total_users * 100) if total_users > 0 else 0
    logger.info(
        f"📊 Envío masivo completado: {sent_count}/{total_users} exitosos ({success_rate:.1f}%)")

    if use_template:
        logger.info(f"📋 Plantilla utilizada: {template_name}")

    logger.info(f"❌ Fallos totales: {failed_count}")


@csrf_exempt
@require_POST
def send_template_message_api(request):
    """
    API endpoint específico para enviar mensajes usando plantillas de Meta
    """
    try:
        # Verificar autenticación
        api_key = request.headers.get(
            'Authorization', '').replace('Bearer ', '')
        if not api_key or api_key != getattr(settings, 'ADMIN_API_KEY', 'admin_secret_key'):
            return JsonResponse({
                "status": "error",
                "message": "No autorizado"
            }, status=401)

        # Obtener datos de la solicitud
        data = json.loads(request.body) if request.body else {}

        template_name = data.get('template_name')
        template_language = data.get('template_language', 'es')
        template_params = data.get('template_params', [])
        # Lista de números específicos
        phone_numbers = data.get('phone_numbers', [])
        # Enviar a todos los usuarios
        send_to_all = data.get('send_to_all', False)
        dry_run = data.get('dry_run', False)

        if not template_name:
            return JsonResponse({
                "status": "error",
                "message": "Se requiere template_name"
            }, status=400)

        # Determinar destinatarios
        if send_to_all:
            users = User.objects.filter(
                platform__in=['WHATSAPP', 'MULTIPLTAFORMA'],
                phone_number__isnull=False
            ).exclude(phone_number='')
            target_numbers = [user.phone_number for user in users]
        elif phone_numbers:
            target_numbers = phone_numbers
        else:
            return JsonResponse({
                "status": "error",
                "message": "Se requiere phone_numbers o send_to_all=true"
            }, status=400)

        total_numbers = len(target_numbers)

        if total_numbers == 0:
            return JsonResponse({
                "status": "error",
                "message": "No se encontraron números para enviar"
            }, status=400)

        if dry_run:
            return JsonResponse({
                "status": "success",
                "message": "Vista previa de plantilla generada",
                "total_numbers": total_numbers,
                "template_name": template_name,
                "template_language": template_language,
                "template_params": template_params,
                # Mostrar solo los primeros 10
                "target_numbers": target_numbers[:10]
            })

        # Enviar plantillas en segundo plano
        import threading
        thread = threading.Thread(
            target=send_template_messages_background,
            args=(target_numbers, template_name,
                  template_language, template_params)
        )
        thread.start()

        return JsonResponse({
            "status": "success",
            "message": f"Envío de plantillas iniciado para {total_numbers} números",
            "total_numbers": total_numbers,
            "template_name": template_name
        })

    except Exception as e:
        logger.exception(f"Error en API de plantilla: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)


def send_template_messages_background(phone_numbers, template_name, template_language="es", template_params=None):
    """Envía plantillas de mensaje en segundo plano"""
    sent_count = 0
    failed_count = 0
    total_numbers = len(phone_numbers)

    logger.info(f"Iniciando envío de plantillas a {total_numbers} números")
    logger.info(f"Plantilla: {template_name} (idioma: {template_language})")

    for i, phone_number in enumerate(phone_numbers, 1):
        try:
            success = send_meta_whatsapp_message(
                phone_number=phone_number,
                message_text="",  # No se usa para plantillas
                use_template=True,
                template_name=template_name,
                template_language=template_language,
                template_params=template_params
            )

            if success:
                sent_count += 1
                logger.info(
                    f"✅ [{i}/{total_numbers}] Plantilla enviada a {phone_number}")
            else:
                failed_count += 1
                logger.error(
                    f"❌ [{i}/{total_numbers}] Falló envío de plantilla a {phone_number}")

            # Delay entre mensajes
            if i < total_numbers:
                time.sleep(2)

        except Exception as e:
            failed_count += 1
            logger.error(
                f"❌ [{i}/{total_numbers}] Error enviando plantilla a {phone_number}: {str(e)}")
            continue

    # Log del resumen final
    success_rate = (sent_count / total_numbers *
                    100) if total_numbers > 0 else 0
    logger.info(
        f"📊 Envío de plantillas completado: {sent_count}/{total_numbers} exitosos ({success_rate:.1f}%)")
    logger.info(f"❌ Fallos totales: {failed_count}")


@csrf_exempt
@require_POST
def schedule_reminder_messages(request):
    """
    Programa mensajes de recordatorio automáticos
    """
    try:
        # Verificar autenticación
        api_key = request.headers.get(
            'Authorization', '').replace('Bearer ', '')
        if not api_key or api_key != getattr(settings, 'ADMIN_API_KEY', 'admin_secret_key'):
            return JsonResponse({
                "status": "error",
                "message": "No autorizado"
            }, status=401)

        data = json.loads(request.body) if request.body else {}

        reminder_type = data.get('type', 'daily')  # daily, weekly, monthly
        template = data.get('template', 'reminder')

        # Obtener usuarios activos (que han enviado mensajes en los últimos 30 días)
        from datetime import datetime, timedelta
        from users.models import Chat, Message

        thirty_days_ago = datetime.now() - timedelta(days=30)

        # Usuarios que han enviado mensajes recientemente
        active_chats = Chat.objects.filter(
            platform='WHATSAPP',
            messages__created_at__gte=thirty_days_ago
        ).distinct()

        active_users = User.objects.filter(
            platform__in=['WHATSAPP', 'MULTIPLTAFORMA'],
            phone_number__isnull=False,
            chats__in=active_chats
        ).exclude(phone_number='').distinct()

        total_users = active_users.count()

        if total_users == 0:
            return JsonResponse({
                "status": "error",
                "message": "No se encontraron usuarios activos"
            }, status=400)

        # Obtener mensaje del template
        message = get_template_message(template)

        if not message:
            return JsonResponse({
                "status": "error",
                "message": "Template no válido"
            }, status=400)

        # Enviar mensajes en segundo plano
        import threading
        thread = threading.Thread(
            target=send_mass_messages_background,
            args=(active_users, message)
        )
        thread.start()

        return JsonResponse({
            "status": "success",
            "message": f"Recordatorio {reminder_type} programado para {total_users} usuarios activos",
            "total_users": total_users,
            "template": template
        })

    except Exception as e:
        logger.exception(f"Error programando recordatorios: {str(e)}")
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)
