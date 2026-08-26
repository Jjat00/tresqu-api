import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from asgiref.sync import sync_to_async
import time
import tempfile
import os
import asyncio
import re
from django.db import transaction, connections, connection, InterfaceError
import pytz

from django.conf import settings
from users.models import User, Chat, Message, TrackingLink
from .services import process_message
from .currencies import COMMON_CURRENCIES, is_valid_currency, get_currency_name

# Configuración de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# Definir estados para la conversación
ESPERANDO_TELEFONO = 0
ESPERANDO_MONEDA = 1
ESPERANDO_MENSAJE_BROADCAST = 2
ESPERANDO_ZONA_HORARIA = 3  # Nuevo estado


# Función de utilidad para normalizar números de teléfono
def normalize_phone_number(phone_number):
    """
    Normaliza un número de teléfono eliminando el signo + al inicio y todos los espacios.
    Maneja casos especiales como números mexicanos.

    Args:
        phone_number (str): El número de teléfono a normalizar

    Returns:
        str: El número normalizado sin el signo + y sin espacios, o None si el input era None
    """
    if not phone_number:
        return None

    # Eliminar espacios al inicio y final primero
    normalized = phone_number.strip()

    # Eliminar el signo + al inicio si existe
    normalized = normalized.lstrip('+')

    # Eliminar todos los espacios, guiones y otros caracteres
    normalized = normalized.replace(' ', '').replace(
        '-', '').replace('(', '').replace(')', '')

    # Caso especial para México: números móviles
    # Si el número empieza con 52 y tiene 12 dígitos, pero no tiene el "1" después del código de país
    # Ejemplo: 525528995412 debería ser 5215528995412
    if normalized.startswith('52') and len(normalized) == 12:
        # Verificar si es un número móvil mexicano (códigos de área móviles comunes)
        # Los códigos de área móviles en México incluyen: 55, 33, 81, 222, etc.
        # Obtener los primeros 2 dígitos después de 52
        area_codes = normalized[2:4]
        mobile_area_codes = ['55', '33', '81', '22', '44',
                             '66', '99', '77', '61', '64', '65', '67', '68', '69']

        if area_codes in mobile_area_codes:
            # Insertar el "1" después del código de país para números móviles
            normalized = '521' + normalized[2:]

    return normalized


# Funciones síncronas para operaciones de base de datos
def get_or_create_chat(chat_id):
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
                    platform='TELEGRAM',
                    platform_chat_id=str(chat_id),
                    defaults={'platform': 'TELEGRAM'}
                )
        except InterfaceError:
            # La conexión se cerró, intentar reconectar
            retry_count += 1
            logger.warning(
                f"Conexión cerrada, intento {retry_count} de reconexión para chat_id={chat_id}")

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
    return User.objects.filter(external_id=external_id).first()


def get_user_by_phone_number(phone_number):
    """Busca un usuario por número de teléfono"""
    # Normalizar el número de teléfono (eliminar el signo + si existe)
    normalized_phone = normalize_phone_number(phone_number)
    return User.objects.filter(phone_number=normalized_phone).first()


def create_user(external_id, platform, first_name, username, phone_number=None, default_currency='USD', source_tracking_link=None):
    # Normalizar el número de teléfono (eliminar el signo + si existe)
    normalized_phone = normalize_phone_number(phone_number)
    user = User.objects.create(
        external_id=external_id,
        platform=platform,
        first_name=first_name or "",
        username=username or "",
        phone_number=normalized_phone,
        default_currency=default_currency,
        source_tracking_link=source_tracking_link
    )

    # Si hay un tracking link, incrementar el contador de registros
    if source_tracking_link:
        source_tracking_link.increment_registrations()
        logger.info(
            f"Usuario {user.id} asociado con TrackingLink: {source_tracking_link.name} ({source_tracking_link.code})")

    return user


def update_chat_user(chat, user):
    chat.user = user
    chat.save()
    return chat


def update_user_currency(user, currency_code):
    """Actualiza la moneda por defecto del usuario"""
    user.default_currency = currency_code
    user.save()
    return user


def get_chat_user(chat_id):
    """Obtiene el usuario asociado a un chat de manera segura con reintentos"""
    max_retries = 3
    retry_count = 0
    backoff_time = 0.5  # tiempo inicial en segundos

    while retry_count < max_retries:
        try:
            with transaction.atomic():
                chat = Chat.objects.get(
                    platform='TELEGRAM', platform_chat_id=str(chat_id))
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
            return None, None


def get_all_telegram_chats():
    """Obtiene todos los chats de Telegram que tienen un usuario asociado"""
    max_retries = 3
    retry_count = 0
    backoff_time = 0.5

    while retry_count < max_retries:
        try:
            with transaction.atomic():
                chats = Chat.objects.filter(
                    platform='TELEGRAM',
                    user__isnull=False).select_related('user')
                return [chat for chat in chats]  # Esto fuerza la evaluación
        except InterfaceError:
            retry_count += 1
            logger.warning(
                f"Conexión cerrada, intento {retry_count} de reconexión para get_all_telegram_chats")

            # Con el pool de conexiones, no necesitamos cerrar manualmente

            if retry_count < max_retries:
                time.sleep(backoff_time)
                backoff_time *= 2
                continue
            else:
                logger.error(
                    f"No se pudo reconectar después de {max_retries} intentos")
                raise
        except Exception as e:
            logger.error(f"Error al obtener todos los chats: {e}")
            raise


def is_admin_user(user_id):
    """Verifica si un usuario es administrador basado en la configuración"""
    admin_ids = getattr(settings, 'TELEGRAM_ADMIN_IDS', [])
    return str(user_id) in [str(admin_id) for admin_id in admin_ids]


# Conversión a funciones asíncronas
get_or_create_chat_async = sync_to_async(get_or_create_chat)
create_message_async = sync_to_async(create_message)
get_user_by_external_id_async = sync_to_async(get_user_by_external_id)
get_user_by_phone_number_async = sync_to_async(get_user_by_phone_number)
create_user_async = sync_to_async(create_user)
update_chat_user_async = sync_to_async(update_chat_user)
update_user_currency_async = sync_to_async(update_user_currency)
get_chat_user_async = sync_to_async(get_chat_user)
get_all_telegram_chats_async = sync_to_async(get_all_telegram_chats)
is_admin_user_async = sync_to_async(is_admin_user)


# Añadir funciones síncronas para manejar la zona horaria
def update_user_timezone(user, timezone_str):
    """Actualiza la zona horaria del usuario"""
    user.timezone = timezone_str
    user.save()
    return user


# Añadir a las funciones asíncronas
update_user_timezone_async = sync_to_async(update_user_timezone)


def extract_referral_code(message_text):
    """
    Extrae el código de referido de un mensaje de Telegram

    Busca patrones como:
    - "Hola, vengo de EMPRESA_ABC_123"
    - "EMPRESA_ABC_123"
    - "Vengo de empresa_abc_123"
    - También maneja parámetros de /start como "/start EMPRESA_ABC_123"

    Returns:
        str: Código de referido encontrado o None
    """
    if not message_text:
        logger.info("extract_referral_code: mensaje vacío")
        return None

    logger.info(f"extract_referral_code: analizando mensaje: '{message_text}'")

    # Patrones para detectar códigos de referido (case-insensitive)
    patterns = [
        r'/start\s+([A-Za-z0-9_]+)',       # "/start EMPRESA_ABC_123"
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


# Funciones asíncronas para referidos
extract_referral_code_async = sync_to_async(extract_referral_code)
find_tracking_link_async = sync_to_async(find_tracking_link)


# Zonas horarias principales para América Latina y algunas ciudades importantes
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


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Permite al usuario seleccionar o actualizar su zona horaria"""
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Obtener el usuario de la base de datos
    user_id, db_user = await get_chat_user_async(chat_id)

    if not db_user:
        await update.message.reply_text(
            "Primero debes registrarte para configurar tu zona horaria. "
            "Usa el comando /registrar para comenzar."
        )
        return ConversationHandler.END

    # Crear un teclado con las zonas horarias principales
    keyboard = []
    for tz_code, tz_name in COMMON_TIMEZONES:
        keyboard.append([InlineKeyboardButton(
            tz_name, callback_data=f"tz:{tz_code}")])

    # Añadir un botón para cancelar
    keyboard.append([InlineKeyboardButton(
        "Cancelar", callback_data="tz:cancel")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"👋 Hola {user.first_name}!\n\n"
        f"Selecciona tu zona horaria para mostrar las fechas y horas correctamente:",
        reply_markup=reply_markup
    )

    return ESPERANDO_ZONA_HORARIA


async def handle_timezone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Procesa la selección de zona horaria del usuario"""
    query = update.callback_query
    await query.answer()

    # Extraer la zona horaria seleccionada del callback_data
    callback_data = query.data

    if callback_data == "tz:cancel":
        await query.edit_message_text("Operación cancelada.")
        return ConversationHandler.END

    timezone_code = callback_data.replace("tz:", "")

    # Obtener el usuario
    chat_id = update.effective_chat.id
    user_id, db_user = await get_chat_user_async(chat_id)

    if not db_user:
        await query.edit_message_text(
            "No se pudo actualizar tu zona horaria. Por favor, registrate primero."
        )
        return ConversationHandler.END

    # Actualizar la zona horaria del usuario
    try:
        # Verificar que la zona horaria es válida
        pytz.timezone(timezone_code)

        # Actualizar en la base de datos
        updated_user = await update_user_timezone_async(db_user, timezone_code)

        # Encontrar el nombre descriptivo de la zona horaria
        tz_name = next(
            (name for code, name in COMMON_TIMEZONES if code == timezone_code), timezone_code)

        await query.edit_message_text(
            f"✅ Tu zona horaria ha sido actualizada a:\n"
            f"{tz_name}\n\n"
            f"Ahora todas las fechas y horas se mostrarán correctamente según tu ubicación."
        )
    except Exception as e:
        logger.error(f"Error al actualizar la zona horaria: {e}")
        await query.edit_message_text(
            f"❌ No se pudo actualizar tu zona horaria. Por favor, intenta nuevamente."
        )

    return ConversationHandler.END


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envía un mensaje cuando se emite el comando /start."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Detectar código de referido en el comando /start
    referral_code = None
    tracking_link = None

    if context.args and len(context.args) > 0:
        # El código viene como parámetro: /start EMPRESA_ABC_123
        potential_code = context.args[0]
        logger.info(f"Parámetro de /start detectado: {potential_code}")

        # Verificar si es un código válido
        tracking_link = await find_tracking_link_async(potential_code)
        if tracking_link:
            referral_code = potential_code
            logger.info(
                f"Código de referido válido en /start: {referral_code}")

            # Guardar en el contexto para usar durante el registro
            context.user_data['referral_code'] = referral_code
            context.user_data['tracking_link_id'] = tracking_link.id

    # Guardar el chat en la base de datos si no existe
    chat, created = await get_or_create_chat_async(chat_id)

    # Verificar si el usuario ya está registrado
    user_id, db_user = await get_chat_user_async(chat_id)

    # Mensaje personalizado según si está registrado o no
    if user_id is not None and db_user is not None:
        welcome_message = (
            f"¡Hola {user.first_name}! Bienvenido de nuevo a Tresqu, tu asistente de finanzas personales.\n\n"
            f"Puedes registrar tus gastos simplemente enviándome mensajes como:\n"
            f"- \"Gasté 50k en comida\"\n"
            f"- \"Compré café por 35000\"\n"
            f"- \"Pagué la cuenta de luz, 75k\"\n\n"
            f"📖 Mira todo lo que puedes hacer con Tresqu: https://tresqu.com/funciones"
        )
    else:
        # Intentar encontrar si existe un usuario registrado con este ID de Telegram
        telegram_external_id = str(user.id)
        existing_user = await get_user_by_external_id_async(telegram_external_id)

        if existing_user:
            # Asociar el usuario existente con este chat
            await update_chat_user_async(chat, existing_user)
            db_user = existing_user

            welcome_message = (
                f"¡Hola {user.first_name}! Tu cuenta ha sido vinculada a este chat.\n\n"
                f"Puedes registrar tus gastos simplemente enviándome mensajes como:\n"
                f"- \"Gasté 50k en comida\"\n"
                f"- \"Compré café por 35000\"\n"
                f"- \"Pagué la cuenta de luz, 75k\"\n\n"
                f"📖 Mira todo lo que puedes hacer con Tresqu: https://tresqu.com/funciones"
            )
        else:
            # Personalizar mensaje si viene de un enlace de referido
            if tracking_link:
                welcome_message = (
                    f"¡Hola {user.first_name}! Bienvenido a Tresqu desde {tracking_link.name}. "
                    f"Soy tu asistente de finanzas personales y puedo ayudarte a registrar gastos y gestionar tu presupuesto.\n\n"
                    f"Para comenzar, necesitas registrarte con tu número de teléfono. "
                    f"Usa el comando /registrar para iniciar el proceso de registro.\n\n"
                    f"Si ya tienes una cuenta en WhatsApp con tu número de teléfono, "
                    f"simplemente comparte tu contacto y vincularemos tu cuenta automáticamente.\n\n"
                    f"📖 Mira todo lo que puedes hacer con Tresqu: https://tresqu.com/funciones"
                )
            else:
                welcome_message = (
                    f"¡Hola {user.first_name}! Soy Tresqu, tu asistente de finanzas personales. "
                    f"Puedo ayudarte a registrar gastos y gestionar tu presupuesto.\n\n"
                    f"Para comenzar, necesitas registrarte con tu número de teléfono. "
                    f"Usa el comando /registrar para iniciar el proceso de registro.\n\n"
                    f"Si ya tienes una cuenta en WhatsApp con tu número de teléfono, "
                    f"simplemente comparte tu contacto y vincularemos tu cuenta automáticamente.\n\n"
                    f"📖 Mira todo lo que puedes hacer con Tresqu: https://tresqu.com/funciones"
                )

            # Crear botón para solicitar número de teléfono
            contact_keyboard = KeyboardButton(
                text="Compartir número de teléfono", request_contact=True)
            reply_markup = ReplyKeyboardMarkup(
                [[contact_keyboard]], one_time_keyboard=True)

            # Enviar el mensaje de bienvenida con el teclado
            await update.message.reply_text(welcome_message, reply_markup=reply_markup)

            # Registrar respuesta
            await create_message_async(
                chat,
                "system",
                "outgoing",
                welcome_message
            )

            return

    # Enviar el mensaje de bienvenida
    await update.message.reply_text(welcome_message)

    # Registrar respuesta
    await create_message_async(
        chat,
        "system",
        "outgoing",
        welcome_message
    )

    # Si el usuario está registrado pero no tiene zona horaria configurada, preguntar
    if db_user and (not hasattr(db_user, 'timezone') or not db_user.timezone):
        # Crear un teclado con las zonas horarias principales
        keyboard = []
        for tz_code, tz_name in COMMON_TIMEZONES:
            keyboard.append([InlineKeyboardButton(
                tz_name, callback_data=f"tz:{tz_code}")])

        # Añadir un botón para cancelar
        keyboard.append([InlineKeyboardButton(
            "Cancelar", callback_data="tz:cancel")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "Para ofrecerte una mejor experiencia, necesito saber tu zona horaria. "
            "Esto me permitirá mostrar fechas y horas correctamente según tu ubicación:",
            reply_markup=reply_markup
        )


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia el proceso de registro solicitando el número de teléfono."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    logger.info(
        f"Iniciando registro para usuario: {user.id} ({user.username or user.first_name})")

    # Detectar código de referido en el mensaje de registro
    if update.message and update.message.text:
        referral_code = await extract_referral_code_async(update.message.text)
        if referral_code:
            tracking_link = await find_tracking_link_async(referral_code)
            if tracking_link:
                logger.info(
                    f"Código de referido detectado en registro: {referral_code}")
                # Guardar en el contexto para usar durante el registro
                context.user_data['referral_code'] = referral_code
                context.user_data['tracking_link_id'] = tracking_link.id

    # Verificar si ya existe un chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        update.message.text
    )

    # Crear botón para solicitar número de teléfono
    contact_keyboard = KeyboardButton(
        text="Compartir número de teléfono", request_contact=True)
    reply_markup = ReplyKeyboardMarkup(
        [[contact_keyboard]], one_time_keyboard=True)

    message = (
        "Para registrarte necesitamos tu número de teléfono. "
        "Por favor, presiona el botón 'Compartir número de teléfono'."
    )

    await update.message.reply_text(message, reply_markup=reply_markup)

    # Registrar respuesta
    await create_message_async(
        chat,
        "system",
        "outgoing",
        message
    )

    return ESPERANDO_TELEFONO


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja mensajes de texto y genera respuestas con procesamiento de lenguaje natural."""
    chat_id = update.effective_chat.id
    user_message_text = update.message.text
    telegram_user = update.effective_user

    logger.info(f"Mensaje recibido de {chat_id}: {user_message_text[:50]}...")

    # Corte anti-bucle por chat: una ráfaga o un eco se descarta antes de tocar
    # la base de datos, y cubre también a quien aún no tiene cuenta (si no,
    # recibiría el mensaje de registro una y otra vez). El filtro de TEMA vive
    # en el agente (agents/relevance_guard).
    from agents import relevance_guard

    blocked = await relevance_guard.check_flood_async(
        relevance_guard.scope_key("telegram", chat_id), user_message_text or ""
    )
    if blocked:
        logger.info(
            f"Mensaje descartado por el guardrail ({blocked.reason}) - chat {chat_id}"
        )
        return

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Obtener el usuario asociado al chat de manera segura
    user_id, chat_user = await get_chat_user_async(chat_id)

    # Si no hay usuario asociado al chat, intentamos encontrarlo por número de teléfono
    # o asociarlo si ya existe en otra plataforma
    if user_id is None or chat_user is None:
        # Si el usuario de Telegram tiene número de teléfono (contacto compartido previamente)
        if hasattr(telegram_user, 'contact') and telegram_user.contact and telegram_user.contact.phone_number:
            # Normalizar el número de teléfono
            phone_number = normalize_phone_number(
                telegram_user.contact.phone_number)

            # Buscar si existe un usuario con este número
            existing_user = await get_user_by_phone_number_async(phone_number)

            if existing_user:
                # Asociar el usuario existente con este chat de Telegram
                await update_chat_user_async(chat, existing_user)
                chat_user = existing_user
                user_id = existing_user.id
                logger.info(
                    f"Usuario existente asociado al chat de Telegram: {existing_user.id}")
        else:
            # Intentar obtener un usuario asociado con este ID de Telegram
            telegram_external_id = str(telegram_user.id)
            existing_user = await get_user_by_external_id_async(telegram_external_id)

            if existing_user:
                # Asociar el usuario existente con este chat
                await update_chat_user_async(chat, existing_user)
                chat_user = existing_user
                user_id = existing_user.id
                logger.info(
                    f"Usuario existente por ID de Telegram asociado al chat: {existing_user.id}")

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        user_message_text
    )

    # Si todavía no hay usuario asociado, solicitar registro
    if user_id is None or chat_user is None:
        message = (
            "Parece que aún no estás registrado. Usa el comando /registrar para crear una cuenta "
            "y poder utilizar todas las funciones del bot.\n\n"
            "Si ya te registraste mediante WhatsApp, necesitarás compartir tu contacto "
            "para que podamos vincular tu cuenta."
        )

        # Crear botón para solicitar número de teléfono
        contact_keyboard = KeyboardButton(
            text="Compartir número de teléfono", request_contact=True)
        reply_markup = ReplyKeyboardMarkup(
            [[contact_keyboard]], one_time_keyboard=True)

        await update.message.reply_text(message, reply_markup=reply_markup)

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            message
        )
        return

    # Verificar si el usuario tiene número de teléfono
    if not chat_user.phone_number:
        message = (
            "Tu cuenta no tiene un número de teléfono registrado. "
            "Por favor, usa el comando /registrar para completar tu registro."
        )
        await update.message.reply_text(message)

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            message
        )
        return

    # Procesar el mensaje para extraer información y generar embedding
    try:
        # Procesar mensaje
        response = await process_message(chat_user, user_message_text)

        # El guardrail de tema cortó el turno (mensaje ajeno a las finanzas o
        # bucle automático): no se responde ni se registra nada.
        if response.silent:
            logger.info(
                f"Mensaje silenciado por el guardrail de tema (usuario {chat_user.id})"
            )
            return

        await update.message.reply_text(response.text, parse_mode="Markdown")

        # Si la herramienta devolvió un preview pendiente de confirmación
        # (Wallbit BUY/SELL/move/resume...), enviar los botones inline
        # justo después del recap textual.
        pending = response.pending_confirmation
        if pending:
            try:
                from .wallbit_handlers import send_confirmation_buttons
                await send_confirmation_buttons(
                    bot=context.bot,
                    chat_id=update.effective_chat.id,
                    decision_id=pending["confirmation_id"],
                    preview=pending.get("preview", {}),
                    two_step=pending.get("two_step_required", False),
                )
            except Exception as exc:
                logger.exception(f"send_confirmation_buttons (telegram) failed: {exc}")

        # Registrar respuesta
        await create_message_async(
            chat,
            "ai_response",
            "outgoing",
            response.text
        )
    except Exception as e:
        logger.error(f"Error al procesar mensaje: {e}")
        error_message = "Lo siento, hubo un error al procesar tu mensaje. Por favor, intenta de nuevo más tarde."
        await update.message.reply_text(error_message)

        # Registrar error
        await create_message_async(
            chat,
            "error",
            "outgoing",
            error_message
        )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Maneja el contacto recibido y solicita la moneda por defecto."""
    logger.info("Iniciando handle_contact")

    chat_id = update.effective_chat.id
    user = update.effective_user
    contact = update.message.contact
    phone_number = contact.phone_number

    logger.info(
        f"Contacto recibido - chat_id: {chat_id}, user_id: {user.id}, phone: {phone_number}")

    # Normalizar el número de teléfono (eliminar el signo + si existe)
    normalized_phone = normalize_phone_number(phone_number)

    # Verificar que el contacto pertenece al usuario que está haciendo el registro
    if str(contact.user_id) != str(user.id):
        await update.message.reply_text(
            "El número de teléfono debe ser el tuyo. Por favor, utiliza el botón para compartir tu contacto.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ESPERANDO_TELEFONO

    logger.info(f"Contacto validado para {user.id}: {normalized_phone}")

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        "Contacto compartido"
    )

    # Verificar si ya existe un usuario con este número de teléfono
    # (desde cualquier plataforma WhatsApp/Telegram)
    existing_user = await get_user_by_phone_number_async(normalized_phone)

    if existing_user:
        # Si ya existe un usuario con este número, asociarlo a este chat
        await update_chat_user_async(chat, existing_user)

        await update.message.reply_text(
            f"¡Bienvenido de nuevo! Tu cuenta ya está vinculada a este chat.\n\n"
            f"Moneda predeterminada: {existing_user.default_currency}\n\n"
            f"Ahora puedes empezar a registrar tus gastos simplemente enviándome mensajes como:\n"
            f"- \"Gasté 50k en comida\"\n"
            f"- \"Compré café por 35000\"\n"
            f"- \"Pagué la cuenta de luz, 75k\"",
            reply_markup=ReplyKeyboardRemove()
        )

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            "Cuenta existente vinculada al chat"
        )

        return ConversationHandler.END

    # Si no existe usuario, continuar con el flujo normal de registro
    # Guardar el número de teléfono normalizado en el contexto para usarlo después
    context.user_data["phone_number"] = normalized_phone
    logger.info("Número de teléfono guardado en context.user_data")

    # Crear botones para monedas comunes usando teclado normal
    keyboard = []
    for i in range(0, len(COMMON_CURRENCIES), 2):
        row = []
        currency = COMMON_CURRENCIES[i]
        row.append(KeyboardButton(f"{currency['flag']} {currency['code']}"))

        # Agregar segunda moneda en la fila si existe
        if i + 1 < len(COMMON_CURRENCIES):
            currency2 = COMMON_CURRENCIES[i + 1]
            row.append(KeyboardButton(
                f"{currency2['flag']} {currency2['code']}"))

        keyboard.append(row)

    # Agregar botón para especificar otra moneda
    keyboard.append([KeyboardButton("Otra moneda")])
    logger.info("Agregado botón 'Otra moneda'")

    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    logger.info("Teclado normal creado")

    message = (
        "¡Gracias! Ahora necesito que selecciones tu moneda predeterminada. "
        "Esta moneda se usará cuando no especifiques una al registrar gastos.\n\n"
        "Selecciona una de las opciones comunes o elige 'Otra moneda' para escribir el código ISO:"
    )

    await update.message.reply_text(message, reply_markup=reply_markup)
    logger.info("Mensaje con teclado enviado")

    # Registrar respuesta
    await create_message_async(
        chat,
        "system",
        "outgoing",
        message
    )

    return ESPERANDO_MONEDA


async def handle_currency_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Maneja la entrada de texto para el código de moneda."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text.strip()

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        text
    )

    # Si el usuario seleccionó "Otra moneda"
    if text == "Otra moneda":
        message = (
            "Por favor, escribe el código ISO 4217 de tu moneda (3 letras).\n"
            "Por ejemplo: USD, EUR, GBP, etc."
        )
        await update.message.reply_text(message, reply_markup=ReplyKeyboardRemove())
        return ESPERANDO_MONEDA

    # Extraer el código de moneda del texto (asumiendo formato "🏦 USD")
    currency_code = text.split(
    )[-1].upper() if len(text.split()) > 1 else text.upper()

    # Validar el código de moneda
    if not is_valid_currency(currency_code):
        message = (
            f"'{currency_code}' no es un código de moneda válido según ISO 4217.\n"
            f"Por favor, escribe un código válido de 3 letras como USD, EUR, GBP, etc."
        )
        await update.message.reply_text(message)
        return ESPERANDO_MONEDA

    # Completar el registro con la moneda proporcionada
    return await complete_registration(update, context, currency_code)


async def complete_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, currency_code: str) -> int:
    """Completa el proceso de registro y configura la moneda predeterminada."""
    logger.info("Iniciando complete_registration")

    user = update.effective_user
    chat_id = update.effective_chat.id
    phone_number = context.user_data.get('phone_number')
    logger.info(
        f"Procesando registro para chat_id: {chat_id}, user: {user.id}")

    if not phone_number:
        logger.error("No se encontró número de teléfono en context.user_data")
        await update.message.reply_text(
            "No se encontró un número de teléfono. Por favor, inicia el registro nuevamente con /registrar."
        )
        return ConversationHandler.END

    # Obtener o crear el chat
    chat, created = await get_or_create_chat_async(chat_id)
    logger.info("Chat obtenido/creado en complete_registration")

    # Asegurarse de que el número de teléfono esté normalizado
    phone_number = normalize_phone_number(phone_number)
    logger.info(f"Número de teléfono normalizado: {phone_number}")

    # Buscar si ya existe un usuario con este número de teléfono
    db_user = await get_user_by_phone_number_async(phone_number)
    logger.info(f"Usuario existente encontrado: {db_user is not None}")

    try:
        # Si no existe, crear un nuevo usuario
        if not db_user:
            logger.info("Creando nuevo usuario")

            # Obtener tracking link del contexto si existe
            tracking_link = None
            if context.user_data.get('tracking_link_id'):
                try:
                    tracking_link = await sync_to_async(TrackingLink.objects.get)(
                        id=context.user_data['tracking_link_id']
                    )
                    logger.info(
                        f"Usando TrackingLink del contexto: {tracking_link.name} ({tracking_link.code})")
                except TrackingLink.DoesNotExist:
                    logger.warning(
                        f"TrackingLink con ID {context.user_data['tracking_link_id']} no encontrado")

            db_user = await create_user_async(
                external_id=str(user.id),
                platform="telegram",
                first_name=user.first_name,
                username=user.username,
                phone_number=phone_number,
                default_currency=currency_code,
                source_tracking_link=tracking_link
            )
            action = "creada"
        else:
            # Si existe, actualizar la moneda predeterminada
            logger.info("Actualizando usuario existente")
            db_user = await update_user_currency_async(db_user, currency_code)
            action = "actualizada"

        # Asociar el usuario al chat
        await update_chat_user_async(chat, db_user)
        logger.info("Usuario asociado al chat")

        # Personalizar mensaje si viene de un enlace de referido
        success_message = f"¡Registro exitoso! Tu cuenta ha sido {action} correctamente.\n\n"

        if tracking_link and action == "creada":
            success_message += f"¡Gracias por llegar desde {tracking_link.name}! 🎉\n\n"

        success_message += (
            f"Moneda predeterminada: {currency_code} ({get_currency_name(currency_code)})\n\n"
            f"Ahora puedes empezar a registrar tus gastos simplemente enviándome mensajes como:\n"
            f"- \"Gasté 50k en comida\"\n"
            f"- \"Compré café por 35000\"\n"
            f"- \"Pagué la cuenta de luz, 75k\"\n\n"
            f"💹 ¿Inviertes? Conecta Wallbit para ver y operar tus acciones y ETFs de EE. UU. desde el chat: https://tresqu.com/dashboard/account?tab=integraciones\n\n"
            f"Recuerda que puedes cambiar tu moneda en cualquier momento con /moneda\n"
            f"Puedes ver tu dashboard en https://tresqu.com/dashboard/home para revisar tus gastos, ingresos e inversiones."
        )

        await update.message.reply_text(success_message)
        logger.info("Mensaje de confirmación enviado")

        # Limpiar datos de referido del contexto después del registro exitoso
        if 'referral_code' in context.user_data:
            del context.user_data['referral_code']
        if 'tracking_link_id' in context.user_data:
            del context.user_data['tracking_link_id']

        # Preguntar por la zona horaria si no está configurada
        if not hasattr(db_user, 'timezone') or not db_user.timezone:
            logger.info("Solicitando zona horaria")
            # Crear un teclado con las zonas horarias principales
            keyboard = []
            for tz_code, tz_name in COMMON_TIMEZONES:
                keyboard.append([InlineKeyboardButton(
                    tz_name, callback_data=f"tz:{tz_code}")])

            # Añadir un botón para cancelar
            keyboard.append([InlineKeyboardButton(
                "Más tarde", callback_data="tz:cancel")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "Para finalizar, por favor selecciona tu zona horaria. "
                "Esto me permitirá mostrar fechas y horas correctamente según tu ubicación:",
                reply_markup=reply_markup
            )

            return ESPERANDO_ZONA_HORARIA

        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error en complete_registration: {e}")
        await update.message.reply_text(
            "Lo siento, hubo un error al completar tu registro. Por favor, intenta nuevamente."
        )
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela la conversación actual."""
    chat_id = update.effective_chat.id

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        update.message.text
    )

    message = "Proceso de registro cancelado. Puedes intentarlo nuevamente en cualquier momento con /registrar."
    await update.message.reply_text(message, reply_markup=ReplyKeyboardRemove())

    # Registrar respuesta
    await create_message_async(
        chat,
        "system",
        "outgoing",
        message
    )

    return ConversationHandler.END


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja mensajes de voz y procesa el audio para extraer información de gastos."""
    chat_id = update.effective_chat.id
    voice = update.message.voice

    logger.info(
        f"Mensaje de voz recibido de {chat_id}: duración {voice.duration} segundos")

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Obtener el usuario asociado al chat de manera segura
    user_id, chat_user = await get_chat_user_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        "Mensaje de voz"
    )

    # Si no hay usuario asociado, solicitar registro
    if user_id is None or chat_user is None:
        message = (
            "Parece que aún no estás registrado. Usa el comando /registrar para crear una cuenta "
            "y poder utilizar todas las funciones del bot."
        )
        await update.message.reply_text(message)

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            message
        )
        return

    # Verificar si el usuario tiene número de teléfono
    if not chat_user.phone_number:
        message = (
            "Tu cuenta no tiene un número de teléfono registrado. "
            "Por favor, usa el comando /registrar para completar tu registro."
        )
        await update.message.reply_text(message)

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            message
        )
        return

    # Descargar el archivo de audio
    try:
        # Enviar mensaje de espera mientras se procesa el audio
        wait_message = await update.message.reply_text("Procesando tu mensaje de voz, dame un momento...")

        # Obtener el archivo de voz
        voice_file = await context.bot.get_file(voice.file_id)

        # Crear un archivo temporal para guardar el audio
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
            temp_path = temp_file.name
            # Descargar el archivo de voz al archivo temporal
            await voice_file.download_to_drive(custom_path=temp_path)

            # Procesar el mensaje de voz
            try:
                # Transcribir el audio
                from telegrambot.services import transcribe_audio
                transcription = await transcribe_audio(temp_path)

                # Guardar la transcripción en la base de datos
                response_text = ""
                pending = None
                if transcription:
                    await create_message_async(
                        chat,
                        f"transcription_{update.message.message_id}",
                        "incoming",
                        transcription
                    )

                    # Procesar el mensaje con la transcripción
                    agent_response = await process_message(chat_user, transcription)
                    if agent_response.silent:
                        logger.info(
                            f"Nota de voz silenciada por el guardrail de tema "
                            f"(usuario {chat_user.id})"
                        )
                        await context.bot.delete_message(
                            chat_id=chat_id, message_id=wait_message.message_id
                        )
                        try:
                            os.unlink(temp_path)
                        except Exception:
                            pass
                        return
                    response_text = agent_response.text
                    pending = agent_response.pending_confirmation
                else:
                    response_text = "Lo siento, no pude entender el audio. Por favor, intenta de nuevo con un mensaje de texto o un audio más claro."
            except Exception as e:
                logger.error(f"Error al transcribir audio: {e}")
                response_text = "Lo siento, hubo un error al procesar tu mensaje de voz. Por favor, intenta de nuevo."
                pending = None

            # Eliminar el mensaje de espera
            await context.bot.delete_message(chat_id=chat_id, message_id=wait_message.message_id)

            # Enviar la respuesta
            await update.message.reply_text(response_text, parse_mode="Markdown")

            # Enviar botones de confirmación si la respuesta incluye un preview pendiente
            if pending:
                try:
                    from .wallbit_handlers import send_confirmation_buttons
                    await send_confirmation_buttons(
                        bot=context.bot,
                        chat_id=update.effective_chat.id,
                        decision_id=pending["confirmation_id"],
                        preview=pending.get("preview", {}),
                        two_step=pending.get("two_step_required", False),
                    )
                except Exception as exc:
                    logger.exception(f"send_confirmation_buttons (telegram voice) failed: {exc}")

            # Registrar respuesta
            await create_message_async(
                chat,
                "ai_response",
                "outgoing",
                response_text
            )

        # Eliminar el archivo temporal
        try:
            os.unlink(temp_path)
        except Exception as e:
            logger.warning(f"No se pudo eliminar el archivo temporal: {e}")

    except Exception as e:
        logger.error(f"Error al procesar mensaje de voz: {e}")
        error_message = "Lo siento, hubo un error al procesar tu mensaje de voz. Por favor, intenta de nuevo más tarde."
        await update.message.reply_text(error_message)

        # Registrar error
        await create_message_async(
            chat,
            "error",
            "outgoing",
            error_message
        )


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia el proceso para enviar mensajes a todos los usuarios."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Verificar si el usuario es administrador
    is_admin = await is_admin_user_async(user.id)

    if not is_admin:
        await update.message.reply_text(
            "Lo siento, solo los administradores pueden enviar mensajes masivos a los usuarios."
        )
        return ConversationHandler.END

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        update.message.text
    )

    message = (
        "Estás a punto de enviar un mensaje a todos los usuarios registrados.\n"
        "Por favor, escribe el mensaje que deseas enviar:\n\n"
        "Puedes incluir:\n"
        "- Texto simple\n"
        "- Emojis 😊\n"
        "- Formato *negrita*, _cursiva_, `código`\n\n"
        "Para cancelar este proceso, escribe /cancel"
    )

    await update.message.reply_text(message)

    # Registrar respuesta
    await create_message_async(
        chat,
        "system",
        "outgoing",
        message
    )

    return ESPERANDO_MENSAJE_BROADCAST


async def send_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Envía el mensaje a todos los usuarios registrados."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    broadcast_message = update.message.text

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        broadcast_message
    )

    # Enviar mensaje de confirmación
    confirm_message = "Enviando mensaje a todos los usuarios. Por favor, espera..."
    await update.message.reply_text(confirm_message)

    # Enviar mensaje a todos los usuarios
    chats = await get_all_telegram_chats_async()
    sent_count = 0
    error_count = 0

    for telegram_chat in chats:
        try:
            # No enviar al administrador que está haciendo el broadcast
            if str(telegram_chat.platform_chat_id) != str(chat_id):
                await context.bot.send_message(
                    chat_id=telegram_chat.platform_chat_id,
                    text=f"📣 *Mensaje de Tresqu*\n\n{broadcast_message}",
                    parse_mode="Markdown"
                )

                # Registrar mensaje enviado en la base de datos
                await create_message_async(
                    telegram_chat,
                    "broadcast",
                    "outgoing",
                    broadcast_message
                )

                sent_count += 1

                # Pequeña pausa para evitar limitaciones de la API de Telegram
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(
                f"Error al enviar mensaje a {telegram_chat.platform_chat_id}: {e}")
            error_count += 1

    # Mensaje de resumen
    summary = (
        f"✅ Mensaje enviado a {sent_count} usuarios.\n"
        f"❌ Errores al enviar a {error_count} usuarios."
    )

    await update.message.reply_text(summary)

    # Registrar resumen
    await create_message_async(
        chat,
        "system",
        "outgoing",
        summary
    )

    return ConversationHandler.END


async def handle_contact_shared(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja un contacto compartido fuera del flujo de registro formal."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    contact = update.message.contact
    phone_number = contact.phone_number

    logger.info(
        f"Contacto compartido (fuera de registro) - chat_id: {chat_id}, user_id: {user.id}, phone: {phone_number}")

    # Normalizar el número de teléfono
    normalized_phone = normalize_phone_number(phone_number)

    # Verificar que el contacto pertenece al usuario
    if str(contact.user_id) != str(user.id):
        await update.message.reply_text(
            "El número de teléfono debe ser el tuyo para vincular tu cuenta.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        "Contacto compartido"
    )

    # Verificar si ya existe un usuario con este número de teléfono
    existing_user = await get_user_by_phone_number_async(normalized_phone)

    if existing_user:
        # Si ya existe un usuario con este número, asociarlo a este chat
        await update_chat_user_async(chat, existing_user)

        await update.message.reply_text(
            f"¡Genial! Tu cuenta existente ha sido vinculada a este chat de Telegram.\n\n"
            f"Moneda predeterminada: {existing_user.default_currency}\n\n"
            f"Ahora puedes utilizar Tresqu tanto en WhatsApp como en Telegram con la misma cuenta.",
            reply_markup=ReplyKeyboardRemove()
        )

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            "Cuenta existente vinculada al chat"
        )
    else:
        # Si no existe un usuario con este número, informar que debe registrarse
        await update.message.reply_text(
            f"No encontramos una cuenta asociada al número {normalized_phone}.\n\n"
            f"Para crear una nueva cuenta, usa el comando /registrar.",
            reply_markup=ReplyKeyboardRemove()
        )

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            "No se encontró cuenta existente"
        )


def setup_bot():
    """Configura la aplicación del bot con todos los manejadores"""
    # Configurar el token desde settings.py
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Registrar el conversation handler para el registro
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("registrar", register_user)],
        states={
            ESPERANDO_TELEFONO: [
                MessageHandler(filters.CONTACT, handle_contact),
            ],
            ESPERANDO_MONEDA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               handle_currency_text),
            ],
            ESPERANDO_ZONA_HORARIA: [
                CallbackQueryHandler(
                    handle_timezone_selection, pattern=r"^tz:"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancel)],
        name="registration_conversation",
        persistent=False,
    )
    application.add_handler(conv_handler)

    # Registrar el conversation handler para broadcast (admin)
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", start_broadcast)],
        states={
            ESPERANDO_MENSAJE_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               send_broadcast_message),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancel)],
    )
    application.add_handler(broadcast_handler)

    # Manejar el comando /timezone independientemente
    timezone_handler = ConversationHandler(
        entry_points=[CommandHandler("timezone", timezone_command)],
        states={
            ESPERANDO_ZONA_HORARIA: [
                CallbackQueryHandler(
                    handle_timezone_selection, pattern=r"^tz:"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancel)],
    )
    application.add_handler(timezone_handler)

    # Comandos básicos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("moneda", register_user))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("ayuda", start))

    # Manejar contactos compartidos fuera del flujo de registro
    application.add_handler(MessageHandler(
        filters.CONTACT & ~filters.COMMAND, handle_contact_shared))

    # Mensajes de voz
    application.add_handler(MessageHandler(
        filters.VOICE, handle_voice_message))

    # Todos los demás mensajes de texto
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message))

    # Agregar manejador de errores
    application.add_error_handler(error_handler)

    # Callbacks específicos de Wallbit (Confirmar / Cancelar) — debe ir
    # ANTES del debug_callback global para que ese no los intercepte.
    from .wallbit_handlers import wallbit_callback_handler
    application.add_handler(
        CallbackQueryHandler(
            wallbit_callback_handler,
            pattern=r"^wallbit_(confirm|cancel)_\d+$",
        )
    )

    # Agregar manejador de callback query global para debugging
    application.add_handler(CallbackQueryHandler(debug_callback))

    return application


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja los errores que ocurren durante el procesamiento de actualizaciones."""
    logger.error(f"Error en el bot: {context.error}")

    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Lo siento, ha ocurrido un error. Por favor, intenta nuevamente."
        )


async def debug_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador de debug para callback queries no manejados."""
    query = update.callback_query
    logger.info(f"Callback query no manejado recibido: {query.data}")
    await query.answer("Callback recibido pero no manejado")
