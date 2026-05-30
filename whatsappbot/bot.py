import logging
import json
import requests
import time
import urllib.parse
from django.db import transaction, connections, connection, InterfaceError
from django.conf import settings
from asgiref.sync import sync_to_async

from users.models import User, Chat, Message, TrackingLink
from .services import process_message
from .utils import normalize_phone_number
import re

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


def create_user(external_id, platform, first_name, username=None, phone_number=None, default_currency='USD', source_tracking_link=None):
    """Crea un nuevo usuario"""
    # Normalizar el número de teléfono (eliminar el signo + si existe)
    normalized_phone = normalize_phone_number(phone_number)
    return User.objects.create(
        external_id=external_id,
        platform=platform,
        first_name=first_name or "",
        username=username or "",
        phone_number=normalized_phone,
        default_currency=default_currency,
        source_tracking_link=source_tracking_link
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


# Estados de registro para WhatsApp
ESTADO_INICIAL = 0
ESPERANDO_MONEDA = 1
ESPERANDO_ZONA_HORARIA = 2
REGISTRO_COMPLETO = 3

# Almacenamiento temporal de estados de usuarios de WhatsApp
# Formato: {phone_number: {"estado": ESTADO, "datos": {key: value}}}
whatsapp_user_states = {}

# Monedas comunes (igual que en telegram)
COMMON_CURRENCIES = [
    {'code': 'USD', 'name': 'Dólar estadounidense', 'flag': '🇺🇸'},
    {'code': 'EUR', 'name': 'Euro', 'flag': '🇪🇺'},
    {'code': 'COP', 'name': 'Peso colombiano', 'flag': '🇨🇴'},
    {'code': 'MXN', 'name': 'Peso mexicano', 'flag': '🇲🇽'},
    {'code': 'ARS', 'name': 'Peso argentino', 'flag': '🇦🇷'},
    {'code': 'CLP', 'name': 'Peso chileno', 'flag': '🇨🇱'},
    {'code': 'PEN', 'name': 'Sol peruano', 'flag': '🇵🇪'},
    {'code': 'BRL', 'name': 'Real brasileño', 'flag': '🇧🇷'},
    {'code': 'CAD', 'name': 'Dólar canadiense', 'flag': '🇨🇦'},
    {'code': 'GBP', 'name': 'Libra esterlina', 'flag': '🇬🇧'},
    {'code': 'JPY', 'name': 'Yen japonés', 'flag': '🇯🇵'},
    {'code': 'CNY', 'name': 'Yuan chino', 'flag': '🇨🇳'},
    {'code': 'AUD', 'name': 'Dólar australiano', 'flag': '🇦🇺'},
    {'code': 'VES', 'name': 'Bolívar venezolano', 'flag': '🇻🇪'},
    {'code': 'BOB', 'name': 'Boliviano', 'flag': '🇧🇴'},
    {'code': 'UYU', 'name': 'Peso uruguayo', 'flag': '🇺🇾'},
    {'code': 'PYG', 'name': 'Guaraní paraguayo', 'flag': '🇵🇾'},
    {'code': 'INR', 'name': 'Rupia india', 'flag': '🇮🇳'},
]

# Zonas horarias comunes
COMMON_TIMEZONES = [
    ('America/Bogota', 'Colombia, Ecuador, Perú, Panamá (UTC-5)'),
    ('America/Mexico_City', 'México (UTC-6)'),
    ('America/Santiago', 'Chile (UTC-4/UTC-3)'),
    ('America/Argentina/Buenos_Aires', 'Argentina (UTC-3)'),
    ('America/Caracas', 'Venezuela (UTC-4)'),
    ('America/La_Paz', 'Bolivia (UTC-4)'),
    ('America/Lima', 'Perú (UTC-5)'),
    ('America/Sao_Paulo', 'Brasil - São Paulo (UTC-3)'),
    ('Europe/Madrid', 'España (UTC+1/UTC+2)'),
    ('America/New_York', 'Estados Unidos - Este (UTC-5/UTC-4)'),
    ('America/Los_Angeles', 'Estados Unidos - Oeste (UTC-8/UTC-7)'),
]


def is_valid_currency(currency_code):
    """Verifica si un código de moneda es válido"""
    if not currency_code or len(currency_code) != 3:
        return False

    # Verificar en la lista de monedas comunes
    for currency in COMMON_CURRENCIES:
        if currency['code'] == currency_code:
            return True

    # Si no está en las comunes, pero tiene formato válido, aceptarlo
    return currency_code.isalpha() and currency_code.isupper()


def update_user_timezone(user, timezone_str):
    """Actualiza la zona horaria del usuario"""
    user.timezone = timezone_str
    user.save()
    return user


def extract_referral_code(message_text):
    """
    Extrae el código de referido de un mensaje de WhatsApp

    Busca patrones como:
    - "Hola, vengo de EMPRESA_ABC_123"
    - "EMPRESA_ABC_123"
    - "Vengo de empresa_abc_123"

    Returns:
        str: Código de referido encontrado o None
    """
    if not message_text:
        logger.info("extract_referral_code: mensaje vacío")
        return None

    logger.info(f"extract_referral_code: analizando mensaje: '{message_text}'")

    # Patrones para detectar códigos de referido (case-insensitive)
    patterns = [
        r'vengo\s+de\s+([A-Za-z0-9_]+)',  # "vengo de EMPRESA_ABC_123"
        r'desde\s+([A-Za-z0-9_]+)',       # "desde EMPRESA_ABC_123"
        r'^([A-Za-z0-9_]{5,})$',          # Solo el código "EMPRESA_ABC_123"
        r'código\s+([A-Za-z0-9_]+)',      # "código EMPRESA_ABC_123"
        r'referido\s+([A-Za-z0-9_]+)',    # "referido EMPRESA_ABC_123"
    ]

    # Normalizar el texto (limpiar espacios extra)
    normalized_text = message_text.strip()
    logger.info(
        f"extract_referral_code: texto normalizado: '{normalized_text}'")

    for i, pattern in enumerate(patterns):
        logger.info(f"extract_referral_code: probando patrón {i+1}: {pattern}")
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if match:
            code = match.group(1)
            logger.info(
                f"✅ Código de referido detectado: {code} (patrón {i+1})")
            return code
        else:
            logger.info(f"extract_referral_code: patrón {i+1} no coincide")

    logger.info("extract_referral_code: ningún patrón coincidió")
    return None


def find_tracking_link(referral_code):
    """
    Busca un TrackingLink activo por código

    Args:
        referral_code (str): Código de referido a buscar

    Returns:
        TrackingLink: Enlace encontrado o None
    """
    if not referral_code:
        logger.info("find_tracking_link: código vacío")
        return None

    logger.info(f"find_tracking_link: buscando código: '{referral_code}'")

    try:
        # Primero verificar si existe algún TrackingLink con ese código (sin filtros)
        all_links = TrackingLink.objects.filter(code__iexact=referral_code)
        logger.info(
            f"find_tracking_link: encontrados {all_links.count()} enlaces con código '{referral_code}'")

        for link in all_links:
            logger.info(
                f"find_tracking_link: enlace encontrado - ID: {link.id}, Código: '{link.code}', Activo: {link.is_active}, Expirado: {link.is_expired}")

        tracking_link = TrackingLink.objects.get(
            code__iexact=referral_code,  # Búsqueda case-insensitive
            is_active=True
        )

        # Verificar si no ha expirado
        if tracking_link.is_expired:
            logger.warning(f"❌ Código de referido expirado: {referral_code}")
            return None

        logger.info(
            f"✅ TrackingLink encontrado: {tracking_link.name} ({tracking_link.code})")
        return tracking_link

    except TrackingLink.DoesNotExist:
        logger.warning(
            f"❌ Código de referido no encontrado en DB o no activo: {referral_code}")
        return None
    except Exception as e:
        logger.error(f"Error buscando TrackingLink: {e}")
        return None


def _looks_like_categorization(message_text: str) -> bool:
    """
    Heurística + LLM para decidir si un mensaje del usuario debe interpretarse
    como la categoría de un gasto pendiente, o como un mensaje nuevo
    (registrar otro gasto/ingreso, pregunta, saludo, etc.).

    Se usa cuando hay un gasto de Gmail pendiente de categorizar pero el
    usuario NO respondió con "swipe to reply" al mensaje de notificación.
    """
    if not message_text:
        return False

    text = message_text.strip()

    # Heurística barata: mensajes con dígitos casi nunca son una categoría,
    # pero sí son típicos de un registro de gasto/ingreso ("gaste 20k en pan",
    # "me pagaron 500", "compré café 5000").
    if any(ch.isdigit() for ch in text):
        return False

    # Mensajes muy cortos (1-4 palabras) y sin dígitos los consideramos
    # candidatos a categoría y delegamos al LLM solo si hay duda.
    word_count = len(text.split())
    if word_count <= 4:
        return _llm_intent_is_categorization(text)

    # Mensajes largos sin dígitos rara vez son una sola categoría — delegar al LLM.
    return _llm_intent_is_categorization(text)


def _get_quoted_message_text(chat, platform_message_id: str) -> str | None:
    """
    Busca en la BD el texto de un mensaje anterior del mismo chat por su
    platform_message_id (wamid). Se usa para que el agente sepa sobre qué
    mensaje está respondiendo el usuario cuando hace swipe to reply.
    Trunca para no inflar el prompt.
    """
    if not platform_message_id:
        return None
    try:
        msg = Message.objects.filter(
            chat=chat, platform_message_id=platform_message_id
        ).first()
        if not msg or not msg.text:
            return None
        text = msg.text.strip()
        return text[:500] + ("…" if len(text) > 500 else "")
    except Exception as e:
        logger.error(f"Error obteniendo mensaje citado {platform_message_id}: {e}")
        return None


def _llm_intent_is_categorization(text: str) -> bool:
    """
    Llama a un LLM ligero (gpt-4o-mini) para decidir si el texto es una
    respuesta de categorización o un mensaje nuevo. Falla abierto a False
    (tratarlo como mensaje nuevo) para no secuestrar al usuario si el
    clasificador está caído.
    """
    try:
        from django.conf import settings
        from langchain_openai import ChatOpenAI
        from langchain_core.prompts import ChatPromptTemplate

        intent_llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=settings.OPENAI_API_KEY,
            timeout=10,
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", """Decide si el mensaje del usuario es la CATEGORÍA de un gasto pendiente
o es un MENSAJE NUEVO (registrar otro gasto/ingreso, pregunta, saludo, etc.).

Contexto: el usuario recibió antes una notificación pidiéndole clasificar un gasto.

Responde ÚNICAMENTE con una sola palabra en minúscula:
- "categoria" si el mensaje es el nombre de una categoría (ej: "alimentación", "transporte", "ocio", "comida", "streaming", "salud", "mercado").
- "otro" en cualquier otro caso: si menciona montos, verbos de registrar ("gasté", "pagué", "compré", "gané"), preguntas, saludos, agradecimientos, o texto no relacionado."""),
            ("human", "{text}"),
        ])

        chain = prompt | intent_llm
        response = chain.invoke({"text": text})
        answer = (response.content or "").strip().lower()
        logger.info(f"Intent clasificador: '{text[:40]}' → '{answer}'")
        return answer.startswith("categoria")
    except Exception as e:
        logger.error(f"Error en clasificador de intención: {e}")
        return False


def associate_user_with_tracking_link(user, tracking_link):
    """
    Asocia un usuario con un enlace de seguimiento e incrementa el contador

    Args:
        user (User): Usuario a asociar
        tracking_link (TrackingLink): Enlace de seguimiento
    """
    try:
        # Asociar el usuario con el enlace
        user.source_tracking_link = tracking_link
        user.save()

        # Incrementar el contador de registros
        tracking_link.increment_registrations()

        logger.info(
            f"Usuario {user.id} asociado con TrackingLink {tracking_link.name}")
        logger.info(
            f"Total registros para {tracking_link.code}: {tracking_link.total_registrations}")

    except Exception as e:
        logger.error(f"Error asociando usuario con TrackingLink: {e}")


async def handle_whatsapp_message(sender_number, message_text, message_id, instance_name=None, server_url=None, api_key=None, sender_name=None, message_type="text", media_url=None, replied_to_message_id=None):
    """
    Procesa un mensaje entrante de WhatsApp y envía una respuesta usando Meta WhatsApp API

    Args:
        sender_number: Número de teléfono del remitente
        message_text: Texto del mensaje
        message_id: ID único del mensaje
        instance_name: Nombre de la instancia (no usado en Meta API)
        server_url: URL del servidor (no usado en Meta API)
        api_key: Clave API (no usado en Meta API)
        sender_name: Nombre del remitente (opcional)
        message_type: Tipo de mensaje (text, audio, image, etc.)
        media_url: URL del contenido multimedia (si aplica)
        replied_to_message_id: wamid del mensaje anterior al que el usuario responde
            (cuando usa la función "swipe to reply" de WhatsApp). None si no aplica.
    """
    try:
        # Variable para controlar si saltamos la verificación de mensajes duplicados
        skip_duplicate_check = False

        # Verificar tipo de mensaje
        if message_type in ["audio", "voice", "ptt"]:
            # Procesar mensaje de audio/voz
            logger.info(f"Procesando mensaje de voz de {sender_number}")

            # Crear chat si no existe
            chat, created = await sync_to_async(get_or_create_chat)(sender_number)

            # Verificar si tenemos el media_id para descargar el audio
            if not media_url:
                response_text = "Lo siento, no pude acceder al archivo de audio. Por favor, intenta de nuevo."

                await sync_to_async(create_message)(
                    chat, f"response_{message_id}", "outgoing", response_text
                )

                success = await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=response_text
                )

                return success, response_text

            try:
                # Enviar mensaje de "procesando"
                processing_message = "🎧 Procesando tu mensaje de voz..."
                await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=processing_message
                )

                # Descargar el archivo de audio usando la API de Meta
                from whatsappbot.services import download_whatsapp_media, transcribe_audio
                from django.conf import settings

                # Obtener el token de acceso de Meta
                access_token = getattr(
                    settings, 'META_WHATSAPP_ACCESS_TOKEN', '')

                if not access_token:
                    logger.error(
                        "Token de acceso de Meta WhatsApp no configurado")
                    response_text = "Lo siento, hay un problema de configuración. Por favor, contacta al soporte."
                else:
                    # Descargar el archivo de audio
                    temp_audio_path = await download_whatsapp_media(media_url, access_token)

                    if not temp_audio_path:
                        response_text = "Lo siento, no pude descargar el archivo de audio. Por favor, intenta de nuevo."
                    else:
                        # Transcribir el audio
                        transcription = await transcribe_audio(temp_audio_path)

                        # Limpiar el archivo temporal
                        import os
                        try:
                            os.unlink(temp_audio_path)
                        except:
                            pass

                        if not transcription or not transcription.strip():
                            response_text = "Lo siento, no pude entender el audio. Por favor, intenta de nuevo con un mensaje de texto o un audio más claro."
                        else:
                            # Procesar el texto transcrito como un mensaje normal
                            logger.info(
                                f"Transcripción exitosa: {transcription}")

                            # Guardar el mensaje original (voz) y la transcripción
                            await sync_to_async(create_message)(
                                chat, message_id, "incoming", f"[Mensaje de voz]: {transcription}"
                            )

                            # Actualizar el message_text con la transcripción y continuar el procesamiento normal
                            message_text = transcription
                            message_type = "text"  # Cambiar el tipo para procesamiento normal

                            # Saltar la verificación de mensaje duplicado ya que acabamos de guardarlo
                            skip_duplicate_check = True

            except Exception as e:
                logger.error(f"Error procesando mensaje de voz: {e}")
                response_text = "Lo siento, hubo un error procesando tu mensaje de voz. Por favor, intenta de nuevo."

                await sync_to_async(create_message)(
                    chat, f"response_{message_id}", "outgoing", response_text
                )

                success = await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=response_text
                )

                return success, response_text

        elif message_type in ["image", "photo"]:
            # Procesar mensaje con imagen - extraer gastos de factura
            logger.info(f"Procesando imagen de {sender_number}")

            # Crear chat si no existe
            chat, created = await sync_to_async(get_or_create_chat)(sender_number)

            # Verificar si tenemos el media_id para descargar la imagen
            if not media_url:
                response_text = "Lo siento, no pude acceder a la imagen. Por favor, intenta de nuevo."

                await sync_to_async(create_message)(
                    chat, f"response_{message_id}", "outgoing", response_text
                )

                success = await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=response_text
                )

                return success, response_text

            try:
                # Enviar mensaje de "procesando"
                processing_message = "📸 Procesando tu imagen para extraer los gastos..."
                await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=processing_message
                )

                # Descargar el archivo de imagen usando la API de Meta
                from whatsappbot.services import download_whatsapp_image, extract_expenses_from_image
                from django.conf import settings

                # Obtener el token de acceso de Meta
                access_token = getattr(
                    settings, 'META_WHATSAPP_ACCESS_TOKEN', '')

                if not access_token:
                    logger.error(
                        "Token de acceso de Meta WhatsApp no configurado")
                    response_text = "Lo siento, hay un problema de configuración. Por favor, contacta al soporte."
                else:
                    # Descargar la imagen
                    temp_image_path = await download_whatsapp_image(media_url, access_token)

                    if not temp_image_path:
                        response_text = "Lo siento, no pude descargar la imagen. Por favor, intenta de nuevo."
                    else:
                        # Extraer gastos de la imagen
                        # Para la API de OpenAI Vision, necesitamos convertir el archivo local a URL o base64
                        import base64

                        # Leer la imagen y convertirla a base64
                        with open(temp_image_path, 'rb') as image_file:
                            image_data = base64.b64encode(
                                image_file.read()).decode('utf-8')

                        # Determinar el tipo MIME
                        import mimetypes
                        mime_type, _ = mimetypes.guess_type(temp_image_path)
                        if not mime_type:
                            mime_type = 'image/jpeg'

                        # Crear URL de datos
                        image_url = f"data:{mime_type};base64,{image_data}"

                        extracted_expenses = await extract_expenses_from_image(image_url)

                        # Limpiar el archivo temporal
                        import os
                        try:
                            os.unlink(temp_image_path)
                        except:
                            pass

                        if not extracted_expenses or not extracted_expenses.strip():
                            response_text = "Lo siento, no pude extraer gastos de la imagen. Por favor, intenta con una imagen más clara o envía los gastos como texto."
                        elif "No se encontraron gastos" in extracted_expenses:
                            response_text = "No encontré gastos en la imagen. ¿Puedes verificar que sea una factura o recibo y enviarla de nuevo? O puedes escribir los gastos manualmente."
                        else:
                            # Procesar el texto extraído como un mensaje normal
                            logger.info(
                                f"Gastos extraídos de imagen: {extracted_expenses}")

                            # Agregar contexto al texto extraído
                            context_message = f"[Gastos extraídos de imagen]\n{extracted_expenses}"

                            # Guardar el mensaje original (imagen) con los gastos extraídos
                            await sync_to_async(create_message)(
                                chat, message_id, "incoming", context_message
                            )

                            # Si había texto con la imagen, agregarlo al contexto
                            if message_text and message_text.strip():
                                extracted_expenses = f"{message_text}\n\n{extracted_expenses}"

                            # Actualizar el message_text con los gastos extraídos y continuar el procesamiento normal
                            message_text = extracted_expenses
                            message_type = "text"  # Cambiar el tipo para procesamiento normal

                            # Saltar la verificación de mensaje duplicado ya que acabamos de guardarlo
                            skip_duplicate_check = True

            except Exception as e:
                logger.error(f"Error procesando imagen: {e}")
                response_text = "Lo siento, hubo un error procesando tu imagen. Por favor, intenta de nuevo o envía los gastos como texto."

                await sync_to_async(create_message)(
                    chat, f"response_{message_id}", "outgoing", response_text
                )

                success = await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=response_text
                )

                return success, response_text

        elif message_type in ["document", "file", "video"]:
            # Documentos, archivos o videos - función no implementada
            response_text = "Lo siento, actualmente no puedo procesar documentos, archivos o videos. Esta función estará disponible próximamente. Por favor, envía un mensaje de texto."

            # Crear chat si no existe
            chat, created = await sync_to_async(get_or_create_chat)(sender_number)

            # Guardar respuesta
            await sync_to_async(create_message)(
                chat, f"response_{message_id}", "outgoing", response_text
            )

            # Enviar respuesta usando Meta API
            success = await send_whatsapp_response(
                instance_name="meta_api",
                to_number=sender_number,
                message=response_text
            )

            return success, response_text

        # 1. Obtener o crear el chat para este número
        chat, created = await sync_to_async(get_or_create_chat)(sender_number)

        # 2. Verificar si el mensaje ya existe en la base de datos (solo si no es un mensaje de voz ya procesado)
        if not skip_duplicate_check:
            existing_message = await sync_to_async(
                lambda: Message.objects.filter(
                    chat=chat,
                    platform_message_id=message_id
                ).first()
            )()

            if existing_message:
                logger.info(
                    f"⚠️ Mensaje ya existe en BD - ID: {message_id}, De: {sender_number}. Ignorando.")
                return True, "Mensaje duplicado ignorado"

            # 3. Guardar el mensaje entrante
            await sync_to_async(create_message)(
                chat, message_id, "incoming", message_text
            )
        else:
            logger.info(
                f"🎧 Saltando verificación de duplicados para mensaje de voz transcrito: {message_id}")

        # 4. Verificar si el número está en proceso de registro
        registro_activo = sender_number in whatsapp_user_states
        estado_registro = whatsapp_user_states.get(
            sender_number, {}).get("estado", ESTADO_INICIAL)

        # 5. Obtener el usuario asociado al chat o por número de teléfono
        user_id, user = await sync_to_async(get_chat_user)(sender_number)

        if not user:
            # Si no hay usuario en el chat, buscar por número de teléfono
            user = await sync_to_async(get_user_by_phone_number)(sender_number)

            # Si encontramos un usuario existente por número de teléfono, asociarlo al chat
            if user:
                await sync_to_async(update_chat_user)(chat, user)
                logger.info(
                    f"Usuario existente encontrado por número y asociado al chat: {user}")
                # NOTA: Los usuarios existentes siguen el flujo normal
                # Los códigos de referido SOLO funcionan para usuarios nuevos durante el registro
            # Si no hay usuario y no hay registro en curso, iniciar proceso de registro
            elif not registro_activo:
                # NUEVO: Detectar código de referido en el primer mensaje
                referral_code = extract_referral_code(message_text)
                tracking_link = None

                if referral_code:
                    tracking_link = await sync_to_async(find_tracking_link)(referral_code)
                    if tracking_link:
                        logger.info(
                            f"Usuario {sender_number} llegó desde: {tracking_link.name}")

                # Iniciar proceso de registro
                whatsapp_user_states[sender_number] = {
                    "estado": ESPERANDO_MONEDA,
                    "datos": {
                        "phone_number": sender_number,
                        "name": sender_name,  # Guardar el nombre si está disponible
                        "referral_code": referral_code,  # NUEVO: Guardar código de referido
                        # NUEVO: Guardar ID del enlace
                        "tracking_link_id": tracking_link.id if tracking_link else None
                    }
                }

                # Mensaje de bienvenida y solicitud de moneda
                monedas_texto = "\n".join(
                    [f"- {c['flag']} {c['code']} ({c['name']})" for c in COMMON_CURRENCIES])

                # NUEVO: Personalizar mensaje si viene de un enlace de referido
                welcome_prefix = "¡Hola! Soy Tresqu, tu asistente de finanzas personales."
                if tracking_link:
                    welcome_prefix = f"¡Hola! Bienvenido desde {tracking_link.name}. Soy Tresqu, tu asistente de finanzas personales."

                response_text = (
                    f"{welcome_prefix} "
                    f"Para comenzar, necesito un poco de información.\n\n"
                    f"Por favor, indica tu moneda predeterminada respondiendo con el código de 3 letras.\n\n"
                    f"Opciones comunes:\n{monedas_texto}\n\n"
                    f"Por ejemplo, escribe 'USD' para dólar estadounidense."
                )

                # Guardar la respuesta en la base de datos
                await sync_to_async(create_message)(
                    chat, f"response_{message_id}", "outgoing", response_text
                )

                # Enviar la respuesta usando Meta API
                success = await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=response_text
                )

                return success, response_text

        # 6. Procesar el estado de registro si está en curso
        if registro_activo:
            if estado_registro == ESPERANDO_MONEDA:
                # Validar código de moneda
                currency_code = message_text.strip().upper()

                if not is_valid_currency(currency_code):
                    response_text = (
                        f"'{currency_code}' no parece ser un código de moneda válido.\n"
                        f"Por favor, ingresa un código de 3 letras como USD, EUR, COP, etc."
                    )
                else:
                    # Actualizar estado y solicitar zona horaria
                    whatsapp_user_states[sender_number]["datos"]["currency"] = currency_code
                    whatsapp_user_states[sender_number]["estado"] = ESPERANDO_ZONA_HORARIA

                    # Preparar opciones de zona horaria
                    zonas_texto = "\n".join(
                        [f"{i+1}. {desc}" for i, (code, desc) in enumerate(COMMON_TIMEZONES)])

                    response_text = (
                        f"¡Excelente! Has elegido {currency_code} como tu moneda predeterminada.\n\n"
                        f"Ahora, selecciona tu zona horaria respondiendo con el número de la opción:\n\n"
                        f"{zonas_texto}"
                    )

                # Guardar y enviar respuesta usando Meta API
                await sync_to_async(create_message)(
                    chat, f"response_{message_id}", "outgoing", response_text
                )

                success = await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=response_text
                )

                return success, response_text

            elif estado_registro == ESPERANDO_ZONA_HORARIA:
                # Procesar selección de zona horaria
                try:
                    seleccion = int(message_text.strip())
                    if seleccion < 1 or seleccion > len(COMMON_TIMEZONES):
                        raise ValueError("Opción fuera de rango")

                    timezone_code = COMMON_TIMEZONES[seleccion-1][0]
                    timezone_name = COMMON_TIMEZONES[seleccion-1][1]

                    # Completar registro
                    datos = whatsapp_user_states[sender_number]["datos"]
                    currency = datos.get("currency", "USD")

                    # Crear usuario (el external_id sigue la misma convención de telegram)
                    external_id = f"wa_{sender_number}"

                    # Usar el nombre real del contacto si está disponible, sino usar nombre genérico
                    name = datos.get("name")
                    if not name or name.strip() == "":
                        name = f"Usuario WhatsApp {sender_number[-4:]}"

                    # NUEVO: Obtener tracking link si existe
                    tracking_link = None
                    tracking_link_id = datos.get("tracking_link_id")
                    if tracking_link_id:
                        try:
                            tracking_link = await sync_to_async(TrackingLink.objects.get)(id=tracking_link_id)
                        except TrackingLink.DoesNotExist:
                            logger.warning(
                                f"TrackingLink con ID {tracking_link_id} no encontrado")

                    # Crear el usuario con tracking link asociado
                    user = await sync_to_async(create_user)(
                        external_id=external_id,
                        platform="WHATSAPP",
                        first_name=name,
                        phone_number=sender_number,
                        default_currency=currency,
                        source_tracking_link=tracking_link
                    )

                    # Actualizar zona horaria
                    user = await sync_to_async(update_user_timezone)(user, timezone_code)

                    # NUEVO: Incrementar contador si hay tracking link
                    if tracking_link:
                        await sync_to_async(tracking_link.increment_registrations)()
                        logger.info(
                            f"Usuario {user.id} asociado exitosamente con {tracking_link.name}")
                        logger.info(
                            f"Total registros para {tracking_link.code}: {tracking_link.total_registrations}")

                    # Asociar el usuario con el chat
                    await sync_to_async(update_chat_user)(chat, user)

                    # Limpiar el estado de registro
                    del whatsapp_user_states[sender_number]

                    response_text = (
                        f"¡Registro exitoso! Tu cuenta ha sido configurada correctamente.\n\n"
                        f"✅ Moneda: {currency}\n"
                        f"✅ Zona horaria: {timezone_name}\n\n"
                        f"Ahora puedes empezar a registrar tus gastos o ingresos simplemente enviándome mensajes como:\n"
                        f"- \"Gasté 50k en comida\"\n"
                        f"- \"Compré café por 35000\"\n"
                        f"- \"Pagué la cuenta de luz, 75k\"\n"
                        f"- \"Gané 100000 pesos en mi negocio\"\n\n"
                        f"💹 ¿Inviertes? Conecta Wallbit para ver y operar tus acciones y ETFs de EE. UU. desde el chat: https://tresqu.com/dashboard/account?tab=integraciones\n\n"
                        f"Puedes ver tu dashboard en https://tresqu.com/dashboard/home para revisar tus gastos, ingresos e inversiones.\n\n"
                        f"Agrega a Tresqu en tus contactos para que puedas enviarle mensajes siempre que quieras."
                    )

                except ValueError:
                    response_text = (
                        f"Por favor, selecciona una opción válida ingresando solo el número.\n"
                        f"Por ejemplo, escribe '1' para seleccionar la primera zona horaria."
                    )

                # Guardar y enviar respuesta usando Meta API
                await sync_to_async(create_message)(
                    chat, f"response_{message_id}", "outgoing", response_text
                )

                success = await send_whatsapp_response(
                    instance_name="meta_api",
                    to_number=sender_number,
                    message=response_text
                )

                return success, response_text

        # 7. Verificar si hay categorización pendiente de Gmail
        # Prioridad:
        #   a) Si el usuario respondió (swipe) a una notificación de compra
        #      específica, categorizar ESE gasto (aunque ya estuviera categorizado
        #      automáticamente — el usuario está corrigiendo).
        #   b) Si no, y hay un pendiente sin categoría, usar el flujo normal
        #      solo cuando el mensaje parece una categoría y no un nuevo gasto
        #      (intent classifier — Task 3).
        try:
            from gmailbot.whatsapp_handler import (
                check_pending_categorization,
                categorize_gmail_expense,
                find_processed_email_by_notification,
            )

            target_processed_email = None
            if replied_to_message_id:
                target_processed_email = await sync_to_async(
                    find_processed_email_by_notification
                )(user, replied_to_message_id)

            if target_processed_email is None:
                pending = await sync_to_async(check_pending_categorization)(user)
                if pending:
                    looks_like_category = await sync_to_async(
                        _looks_like_categorization
                    )(message_text)
                    if looks_like_category:
                        target_processed_email = pending

            if target_processed_email is not None:
                response_text = await sync_to_async(categorize_gmail_expense)(
                    user, target_processed_email, message_text
                )
                # Guardar y enviar respuesta
                await sync_to_async(create_message)(chat, f"response_{message_id}", "outgoing", response_text)
                success = await send_whatsapp_response(instance_name="meta_api", to_number=sender_number, message=response_text)
                return success, response_text
        except ImportError:
            pass  # gmailbot no instalado
        except Exception as e:
            logger.error(f"Error checking Gmail categorization: {e}")

        # 8. Si llegamos aquí, el usuario existe o se ha registrado correctamente
        # Procesar el mensaje y obtener respuesta.
        # Si el usuario respondió (swipe) a un mensaje anterior, darle al agente
        # el texto del mensaje citado como contexto para que pueda responder
        # sobre él (ej: "¿qué gasto era este?" respondiendo a una notificación).
        effective_message_text = message_text
        if replied_to_message_id:
            quoted = await sync_to_async(_get_quoted_message_text)(
                chat, replied_to_message_id
            )
            if quoted:
                effective_message_text = (
                    f"[Respondiendo al mensaje anterior: \"{quoted}\"]\n"
                    f"{message_text}"
                )

        response_text = await process_message(user, effective_message_text, sender_phone=sender_number)

        # 9. Guardar la respuesta en la base de datos
        await sync_to_async(create_message)(
            chat, f"response_{message_id}", "outgoing", response_text
        )

        # 10. Enviar la respuesta usando Meta API
        success = await send_whatsapp_response(
            instance_name="meta_api",
            to_number=sender_number,
            message=response_text
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


async def send_whatsapp_response(instance_name, to_number, message, server_url=None, api_key=None):
    """
    Envía una respuesta a un número de WhatsApp utilizando Meta WhatsApp API

    Args:
        instance_name (str): Nombre de la instancia de WhatsApp (no usado en Meta API)
        to_number (str): Número de teléfono del destinatario
        message (str): Mensaje a enviar
        server_url (str): URL del servidor (no usado en Meta API)
        api_key (str): Clave de API (no usado en Meta API)
    """
    try:
        # Usar Meta WhatsApp API exclusivamente
        from .views import send_meta_whatsapp_message
        import asyncio

        logger.info(
            f"Enviando mensaje Meta API a {to_number}: {message[:50]}...")

        # Ejecutar la función síncrona en un thread separado
        success = await asyncio.to_thread(send_meta_whatsapp_message, to_number, message)

        if success:
            logger.info(f"✅ Mensaje Meta enviado exitosamente a {to_number}")
        else:
            logger.error(f"❌ Error enviando mensaje Meta a {to_number}")

        return success

    except Exception as e:
        logger.exception(f"Error enviando mensaje Meta WhatsApp: {str(e)}")
        return False
