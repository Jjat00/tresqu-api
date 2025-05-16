from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
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

logger = logging.getLogger(__name__)

# Duración de validez del código de verificación (en segundos)
VERIFICATION_CODE_TIMEOUT = 300  # 5 minutos

# Prefijo para las claves en caché
VERIFICATION_CODE_PREFIX = 'whatsapp_verification_code_'


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
            conversation = data.get('message', {}).get('conversation', '')
            message_id = data.get('key', {}).get('id', '')

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

            # Si no hay mensaje o número, ignoramos
            if not conversation or not sender_number:
                logger.warning(
                    "Mensaje sin contenido o sin número de remitente válido")
                return JsonResponse({"status": "success", "message": "Evento procesado (sin mensaje)"})

            # Log del mensaje recibido
            logger.info(
                f"Mensaje recibido de {sender_number} ({sender_name}): {conversation}")

            # Usar handle_whatsapp_message para procesar el mensaje y enviar respuesta
            success, response = asyncio.run(handle_whatsapp_message(
                sender_number=sender_number,
                message_text=conversation,
                message_id=message_id,
                instance_name=instance_name,
                server_url=server_url,
                api_key=api_key,
                sender_name=sender_name
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

        # Obtener la URL del servidor y API key (opcional)
        server_url = data.get('server_url', getattr(
            settings, 'EVOLUTION_API_URL', 'http://localhost:8080'))
        api_key = data.get('apikey', getattr(settings, 'GLOBAL_API_KEY', ''))

        # Generar un código de verificación
        verification_code = generate_verification_code()

        # Almacenar el código en caché con expiración
        cache_key = f"{VERIFICATION_CODE_PREFIX}{phone_number}"
        cache.set(cache_key, verification_code, VERIFICATION_CODE_TIMEOUT)

        # Preparar el mensaje con el código
        message = f"Tu código de verificación para Tresqu es: {verification_code}\n\nEste código expirará en 5 minutos."

        # Enviar el código por WhatsApp
        success = send_whatsapp_response(
            instance_name=instance_name,
            to_number=phone_number,
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

        # Obtener el código almacenado en caché
        cache_key = f"{VERIFICATION_CODE_PREFIX}{phone_number}"
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

            # Formatear número de teléfono para buscar o crear usuario
            # Asegurar que el número no tiene el + inicial para la búsqueda
            if phone_number.startswith('+'):
                phone_number_clean = phone_number[1:]
            else:
                phone_number_clean = phone_number

            # Buscar o crear usuario con este número en WhatsApp
            external_id = f"wa_{phone_number_clean}"

            try:
                # Usar directamente la clase User de users.models
                user = User.objects.get(external_id=external_id)
                user_action = "login"
            except User.DoesNotExist:
                # Obtener el plan de suscripción predeterminado (normalmente BÁSICO con ID=1)
                default_plan = SubscriptionPlan.objects.get(id=1)

                # Intentar extraer el nombre del usuario desde los datos (si está disponible)
                user_name = data.get(
                    'name', f"Usuario de WhatsApp {phone_number_clean}")

                # Crear usuario nuevo con los campos obligatorios
                user = User.objects.create(
                    external_id=external_id,
                    platform="WHATSAPP",
                    first_name=user_name,
                    phone_number=phone_number_clean,
                    subscription_plan=default_plan,
                    default_currency="COP",  # Moneda predeterminada
                    timezone="America/Bogota"  # Zona horaria predeterminada
                )
                user_action = "register"

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
