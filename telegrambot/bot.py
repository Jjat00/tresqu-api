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
from django.core.cache import cache
from users.models import User, Chat, Message, TrackingLink
from users.phone import (
    normalize_phone_number as _normalize_phone_number,
    phone_variants,
)
from .services import process_message
from .currencies import (
    COMMON_CURRENCIES,
    is_valid_currency,
    get_currency_name,
    infer_defaults_from_phone,
)

# Configuración de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# El registro NO usa estados de conversación: el webhook construye una
# Application nueva por cada update (telegrambot/views.py), así que cualquier
# estado en memoria se pierde entre mensajes. Todo el alta se resuelve dentro
# de un solo update y los ajustes posteriores viajan en el callback_data de
# botones inline. El único estado que queda es el del broadcast de admin.
ESPERANDO_MENSAJE_BROADCAST = 2


# El teléfono se escribe igual en todos los canales (users/phone.py): es lo
# único que identifica a la misma persona en Telegram, WhatsApp y la web.
normalize_phone_number = _normalize_phone_number


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
    """Busca un usuario por número de teléfono.

    Busca por todas las formas equivalentes del número. Un mexicano dado de
    alta por WhatsApp está guardado como "521..." y su contacto de Telegram
    llega como "52...": con una búsqueda exacta se le crearía otra cuenta en
    vez de vincular la suya.
    """
    variantes = phone_variants(phone_number)
    if not variantes:
        return None
    return User.objects.filter(phone_number__in=variantes).first()


def create_user(external_id, platform, first_name, username, phone_number=None,
                default_currency='USD', source_tracking_link=None, timezone=None):
    # Normalizar el número de teléfono (eliminar el signo + si existe)
    normalized_phone = normalize_phone_number(phone_number)
    campos = dict(
        external_id=external_id,
        platform=platform,
        first_name=first_name or "",
        username=username or "",
        phone_number=normalized_phone,
        default_currency=default_currency,
        source_tracking_link=source_tracking_link,
    )
    if timezone:
        campos["timezone"] = timezone
    user = User.objects.create(**campos)

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


def complete_existing_user(user, phone_number, currency_code, timezone_code,
                          tracking_link=None):
    """Rellena los datos que falten en una cuenta previa del mismo Telegram.

    Pasa cuando alguien quedó a medias en un intento anterior: el external_id ya
    existe, así que crear otra vez daría IntegrityError.
    """
    campos = []

    if not user.phone_number:
        user.phone_number = normalize_phone_number(phone_number)
        campos.append("phone_number")
    if not user.default_currency:
        user.default_currency = currency_code
        campos.append("default_currency")
    if not user.timezone:
        user.timezone = timezone_code
        campos.append("timezone")
    if tracking_link and not user.source_tracking_link_id:
        user.source_tracking_link = tracking_link
        campos.append("source_tracking_link")
        tracking_link.increment_registrations()

    if campos:
        user.save(update_fields=campos)

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
complete_existing_user_async = sync_to_async(complete_existing_user)
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


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/timezone: muestra los botones de zona horaria. Cada botón se basta a sí mismo."""
    chat_id = update.effective_chat.id
    _, db_user = await get_chat_user_async(chat_id)

    if not db_user:
        await update.message.reply_text(
            "Primero necesitas una cuenta. Comparte tu número con el botón de abajo "
            "y la creo al instante.",
            reply_markup=contact_keyboard_markup(),
        )
        return

    await update.message.reply_text(
        "Selecciona tu zona horaria para que las fechas y horas te cuadren:",
        reply_markup=timezone_keyboard_markup(db_user.timezone),
    )


async def handle_timezone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botones de zona horaria: tz:menu abre el listado, tz:<zona> la aplica."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    _, db_user = await get_chat_user_async(chat_id)

    if not db_user:
        await query.edit_message_text(
            "No encuentro tu cuenta. Comparte tu contacto para crearla."
        )
        return

    if query.data == "tz:cancel":
        await query.edit_message_text(
            "Lo dejamos como está. Puedes cambiarla cuando quieras con /timezone."
        )
        return

    if query.data == "tz:menu":
        await query.edit_message_text(
            "Selecciona tu zona horaria:",
            reply_markup=timezone_keyboard_markup(db_user.timezone),
        )
        return

    timezone_code = query.data.split(":", 1)[1]

    try:
        pytz.timezone(timezone_code)  # valida antes de guardar
        await update_user_timezone_async(db_user, timezone_code)
    except Exception as e:
        logger.error(f"Error al actualizar la zona horaria: {e}")
        await query.edit_message_text(
            "❌ No pude actualizar tu zona horaria. Inténtalo de nuevo con /timezone."
        )
        return

    tz_nombre = next(
        (nombre for codigo, nombre in COMMON_TIMEZONES if codigo == timezone_code),
        timezone_code)
    texto = f"✅ Zona horaria: {tz_nombre}"

    await query.edit_message_text(texto)

    chat, _ = await get_or_create_chat_async(chat_id)
    await create_message_async(chat, "system", "outgoing", texto)


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

            # Fuera del proceso: context.user_data muere con este update
            await remember_tracking_link_async(chat_id, tracking_link.id)

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
                    f"Para empezar, comparte tu número con el botón de abajo: "
                    f"con eso creo tu cuenta al instante.\n\n"
                    f"Si ya usas Tresqu en WhatsApp con ese mismo número, "
                    f"vincularé tu cuenta en lugar de crear otra.\n\n"
                    f"📖 Mira todo lo que puedes hacer con Tresqu: https://tresqu.com/funciones"
                )
            else:
                welcome_message = (
                    f"¡Hola {user.first_name}! Soy Tresqu, tu asistente de finanzas personales. "
                    f"Puedo ayudarte a registrar gastos y gestionar tu presupuesto.\n\n"
                    f"Para empezar, comparte tu número con el botón de abajo: "
                    f"con eso creo tu cuenta al instante.\n\n"
                    f"Si ya usas Tresqu en WhatsApp con ese mismo número, "
                    f"vincularé tu cuenta en lugar de crear otra.\n\n"
                    f"📖 Mira todo lo que puedes hacer con Tresqu: https://tresqu.com/funciones"
                )

            # El botón de contacto es la vía de alta: un toque y la cuenta existe
            await update.message.reply_text(
                welcome_message, reply_markup=contact_keyboard_markup())

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
    if db_user and not db_user.timezone:
        await update.message.reply_text(
            "Para mostrarte bien las fechas y las horas, dime tu zona horaria:",
            reply_markup=timezone_keyboard_markup(),
        )


# ---------------------------------------------------------------------------
# Alta de cuenta en un solo update
#
# El registro no puede apoyarse en un ConversationHandler: el webhook construye
# una Application nueva por cada update (telegrambot/views.py) y gunicorn corre
# varios workers, así que el estado en memoria muere entre mensaje y mensaje.
# Por eso la cuenta se crea completa al recibir el contacto y lo que queda por
# ajustar viaja dentro del callback_data de botones inline, que se resuelven
# solos. Lo único que se guarda entre updates es el enlace de referido, y va a
# la cache compartida, no a context.user_data.
# ---------------------------------------------------------------------------

REFERRAL_CACHE_TTL = 60 * 60 * 24  # un día


def _referral_cache_key(chat_id):
    return f"telegram:referral:{chat_id}"


def remember_tracking_link(chat_id, tracking_link_id):
    """Recuerda el referido detectado en /start hasta que el alta lo consuma."""
    try:
        cache.set(_referral_cache_key(chat_id), tracking_link_id, REFERRAL_CACHE_TTL)
    except Exception as e:
        # Un problema de cache jamás debe impedir un registro
        logger.warning(f"No se pudo guardar el referido del chat {chat_id}: {e}")


def pop_tracking_link(chat_id):
    """Devuelve el TrackingLink pendiente del chat y lo consume."""
    try:
        tracking_link_id = cache.get(_referral_cache_key(chat_id))
    except Exception as e:
        logger.warning(f"No se pudo leer el referido del chat {chat_id}: {e}")
        return None

    if not tracking_link_id:
        return None

    try:
        return TrackingLink.objects.get(id=tracking_link_id)
    except TrackingLink.DoesNotExist:
        logger.warning(f"TrackingLink {tracking_link_id} ya no existe")
        return None
    finally:
        try:
            cache.delete(_referral_cache_key(chat_id))
        except Exception:
            pass


remember_tracking_link_async = sync_to_async(remember_tracking_link)
pop_tracking_link_async = sync_to_async(pop_tracking_link)


def contact_keyboard_markup():
    """Botón que pide el contacto: la única vía de alta en Telegram."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(text="Compartir número de teléfono", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )


def currency_keyboard_markup(current=None):
    """Botones inline de moneda; el código viaja en el callback_data (cur:XXX)."""
    keyboard, fila = [], []
    for currency in COMMON_CURRENCIES:
        etiqueta = f"{currency['flag']} {currency['code']}"
        if current and currency["code"] == current:
            etiqueta = f"✅ {etiqueta}"
        fila.append(InlineKeyboardButton(
            etiqueta, callback_data=f"cur:{currency['code']}"))
        if len(fila) == 3:
            keyboard.append(fila)
            fila = []
    if fila:
        keyboard.append(fila)
    return InlineKeyboardMarkup(keyboard)


def timezone_keyboard_markup(current=None):
    """Botones inline de zona horaria (tz:<zona IANA>)."""
    keyboard = []
    for tz_code, tz_name in COMMON_TIMEZONES:
        etiqueta = f"✅ {tz_name}" if current == tz_code else tz_name
        keyboard.append([InlineKeyboardButton(
            etiqueta, callback_data=f"tz:{tz_code}")])
    keyboard.append([InlineKeyboardButton(
        "Más tarde", callback_data="tz:cancel")])
    return InlineKeyboardMarkup(keyboard)


def account_settings_markup():
    """Atajos que se ofrecen justo después de crear la cuenta."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Cambiar moneda", callback_data="cur:menu"),
        InlineKeyboardButton("Cambiar zona horaria", callback_data="tz:menu"),
    ]])


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/registrar: pide el contacto. La cuenta se crea al recibirlo, en un paso."""
    chat_id = update.effective_chat.id

    chat, _ = await get_or_create_chat_async(chat_id)
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        update.message.text
    )

    # Referido escrito junto al comando: "/registrar EMPRESA_ABC"
    if update.message.text:
        referral_code = await extract_referral_code_async(update.message.text)
        if referral_code:
            tracking_link = await find_tracking_link_async(referral_code)
            if tracking_link:
                logger.info(
                    f"Código de referido detectado en /registrar: {referral_code}")
                await remember_tracking_link_async(chat_id, tracking_link.id)

    _, db_user = await get_chat_user_async(chat_id)
    if db_user and db_user.phone_number:
        message = (
            "Ya tienes una cuenta activa en este chat. ✅\n\n"
            f"Moneda por defecto: {db_user.default_currency}\n\n"
            "Cámbiala con /moneda o ajusta tu zona horaria con /timezone."
        )
        await update.message.reply_text(message)
        await create_message_async(chat, "system", "outgoing", message)
        return

    message = (
        "Para crear tu cuenta necesito tu número de teléfono. "
        "Presiona el botón «Compartir número de teléfono» que aparece abajo.\n\n"
        "Tiene que llegar por el botón: si lo escribes no puedo comprobar que es tuyo."
    )
    await update.message.reply_text(message, reply_markup=contact_keyboard_markup())
    await create_message_async(chat, "system", "outgoing", message)


async def currency_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/moneda [CÓDIGO]: cambia la moneda por defecto sin pasos intermedios."""
    chat_id = update.effective_chat.id

    chat, _ = await get_or_create_chat_async(chat_id)
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        update.message.text
    )

    _, db_user = await get_chat_user_async(chat_id)
    if not db_user:
        message = (
            "Todavía no tienes cuenta. Comparte tu número con el botón de abajo "
            "y la creo al instante."
        )
        await update.message.reply_text(message, reply_markup=contact_keyboard_markup())
        await create_message_async(chat, "system", "outgoing", message)
        return

    # "/moneda EUR" aplica directo: cubre las monedas que no están en los botones
    argumento = context.args[0].upper() if context.args else ""
    if argumento:
        if not is_valid_currency(argumento):
            await update.message.reply_text(
                f"'{argumento}' no es un código ISO 4217 válido. "
                f"Prueba con USD, EUR, COP, CLP, MXN..."
            )
            return

        await update_user_currency_async(db_user, argumento)
        message = (
            f"✅ Tu moneda por defecto ahora es {argumento} "
            f"({get_currency_name(argumento)})."
        )
        await update.message.reply_text(message)
        await create_message_async(chat, "system", "outgoing", message)
        return

    await update.message.reply_text(
        f"Tu moneda por defecto es {db_user.default_currency}. "
        f"Elige otra abajo o escribe /moneda seguido del código que necesites "
        f"(por ejemplo /moneda EUR):",
        reply_markup=currency_keyboard_markup(db_user.default_currency),
    )


async def handle_currency_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botones de moneda: cur:menu abre el listado, cur:<CÓDIGO> lo aplica."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    _, db_user = await get_chat_user_async(chat_id)

    if not db_user:
        await query.edit_message_text(
            "No encuentro tu cuenta. Comparte tu contacto para crearla."
        )
        return

    if query.data == "cur:menu":
        await query.edit_message_text(
            f"Tu moneda por defecto es {db_user.default_currency}. "
            f"Elige la que uses a diario:",
            reply_markup=currency_keyboard_markup(db_user.default_currency),
        )
        return

    currency_code = query.data.split(":", 1)[1].upper()
    if not is_valid_currency(currency_code):
        await query.edit_message_text(
            "Esa moneda no es válida. Puedes fijarla con /moneda USD."
        )
        return

    await update_user_currency_async(db_user, currency_code)

    chat, _ = await get_or_create_chat_async(chat_id)
    texto = (
        f"✅ Moneda por defecto: {currency_code} "
        f"({get_currency_name(currency_code)})."
    )
    await query.edit_message_text(texto)
    await create_message_async(chat, "system", "outgoing", texto)


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

    # Chat sin usuario: puede ser alguien que ya se registró desde otro chat de
    # Telegram. El teléfono no viaja en los mensajes normales (solo en el
    # contacto compartido), así que aquí la única pista es el id de Telegram.
    if user_id is None or chat_user is None:
        existing_user = await get_user_by_external_id_async(str(telegram_user.id))

        if existing_user:
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
            "Todavía no tienes cuenta. Comparte tu número con el botón de abajo "
            "y la creo al instante.\n\n"
            "Tiene que llegar por el botón: si lo escribes no puedo comprobar que es tuyo. "
            "Si ya usas Tresqu en WhatsApp con ese número, vincularé esa misma cuenta."
        )

        await update.message.reply_text(message, reply_markup=contact_keyboard_markup())

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
            "Falta tu número de teléfono para terminar de configurar la cuenta. "
            "Compártelo con el botón de abajo y seguimos."
        )
        await update.message.reply_text(message, reply_markup=contact_keyboard_markup())

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
        pendings = response.pending_confirmations or (
            [response.pending_confirmation] if response.pending_confirmation else []
        )
        if pendings:
            try:
                from .wallbit_handlers import send_confirmation_buttons
                # One keyboard per proposed operation.
                for pending in pendings:
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


async def create_account_from_contact(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                      chat, tg_user, phone_number) -> None:
    """Crea la cuenta con el contacto recibido y la deja lista para usar.

    Moneda y zona horaria salen del prefijo telefónico para no encadenar
    preguntas que exigirían recordar un estado entre updates. Son provisionales:
    los botones del final del mensaje (y /moneda, /timezone) las cambian.
    """
    chat_id = update.effective_chat.id
    currency_code, timezone_code = infer_defaults_from_phone(phone_number)
    tracking_link = await pop_tracking_link_async(chat_id)

    try:
        db_user = await get_user_by_external_id_async(str(tg_user.id))

        if db_user:
            # Intento anterior que quedó a medias: se completa en vez de duplicar
            db_user = await complete_existing_user_async(
                db_user, phone_number, currency_code, timezone_code, tracking_link)
            accion = "recuperada"
        else:
            db_user = await create_user_async(
                external_id=str(tg_user.id),
                platform="telegram",
                first_name=tg_user.first_name,
                username=tg_user.username,
                phone_number=phone_number,
                default_currency=currency_code,
                timezone=timezone_code,
                source_tracking_link=tracking_link,
            )
            accion = "creada"

        await update_chat_user_async(chat, db_user)
    except Exception as e:
        logger.exception(
            f"Error creando la cuenta de Telegram para el chat {chat_id}: {e}")
        error = (
            "No pude crear tu cuenta en este momento. "
            "Vuelve a intentarlo en unos minutos con /registrar."
        )
        await update.message.reply_text(error, reply_markup=ReplyKeyboardRemove())
        await create_message_async(chat, "error", "outgoing", error)
        return

    logger.info(
        f"Cuenta {accion} desde Telegram: usuario {db_user.id}, chat {chat_id}")

    tz_nombre = next(
        (nombre for codigo, nombre in COMMON_TIMEZONES if codigo == db_user.timezone),
        db_user.timezone)

    saludo = f"¡Listo, {tg_user.first_name or 'bienvenido'}!"
    bienvenida = (
        f"{saludo} Tu cuenta ya está creada. 🎉\n\n"
        if accion == "creada"
        else f"{saludo} Retomé la cuenta que habías dejado a medias. ✅\n\n"
    )

    if tracking_link:
        bienvenida += f"Gracias por llegar desde {tracking_link.name}.\n\n"

    bienvenida += (
        f"Moneda: {db_user.default_currency} ({get_currency_name(db_user.default_currency)})\n"
        f"Zona horaria: {tz_nombre}\n\n"
        f"Ya puedes registrar tus gastos escribiéndome con normalidad:\n"
        f"- \"Gasté 50k en comida\"\n"
        f"- \"Compré café por 35000\"\n"
        f"- \"Pagué la cuenta de luz, 75k\"\n\n"
        f"💹 ¿Inviertes? Conecta Wallbit para ver y operar tus acciones y ETFs de "
        f"EE. UU. desde el chat: https://tresqu.com/dashboard/account?tab=integraciones\n\n"
        f"Tu dashboard: https://tresqu.com/dashboard/home"
    )

    await update.message.reply_text(bienvenida, reply_markup=ReplyKeyboardRemove())
    await create_message_async(chat, "system", "outgoing", bienvenida)

    ajustes = "Deduje la moneda y la zona horaria por tu número. ¿Quieres cambiarlas?"
    await update.message.reply_text(ajustes, reply_markup=account_settings_markup())


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
            "Todavía no tienes cuenta. Comparte tu número con el botón de abajo "
            "y la creo al instante."
        )
        await update.message.reply_text(message, reply_markup=contact_keyboard_markup())

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
            "Falta tu número de teléfono para terminar de configurar la cuenta. "
            "Compártelo con el botón de abajo y seguimos."
        )
        await update.message.reply_text(message, reply_markup=contact_keyboard_markup())

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
                    pending = agent_response.pending_confirmations or (
                        [agent_response.pending_confirmation]
                        if agent_response.pending_confirmation else []
                    )
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
                    for item in pending:
                        await send_confirmation_buttons(
                            bot=context.bot,
                            chat_id=update.effective_chat.id,
                            decision_id=item["confirmation_id"],
                            preview=item.get("preview", {}),
                            two_step=item.get("two_step_required", False),
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
    """Punto único de alta: vincula la cuenta que ya existe o crea una nueva.

    Antes este handler solo sabía vincular y, cuando no encontraba cuenta,
    remitía a /registrar. Como el estado del ConversationHandler no sobrevivía
    al siguiente update, ese /registrar devolvía justo aquí: nadie podía darse
    de alta por Telegram.
    """
    chat_id = update.effective_chat.id
    tg_user = update.effective_user
    contact = update.message.contact
    phone_number = normalize_phone_number(contact.phone_number)

    logger.info(
        f"Contacto compartido - chat_id: {chat_id}, user_id: {tg_user.id}")

    # El contacto debe ser el suyo: es lo que acredita que el número le pertenece
    if str(contact.user_id) != str(tg_user.id):
        await update.message.reply_text(
            "Ese contacto no es el tuyo. Usa el botón «Compartir número de teléfono» "
            "para enviarme el tuyo.",
            reply_markup=contact_keyboard_markup(),
        )
        return

    chat, _ = await get_or_create_chat_async(chat_id)
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        "Contacto compartido"
    )

    # Si este chat ya tiene cuenta y lo que falta es el teléfono, se completa
    # esa cuenta: crear otra dejaría huérfanos sus gastos.
    _, cuenta_del_chat = await get_chat_user_async(chat_id)
    if cuenta_del_chat and not cuenta_del_chat.phone_number:
        currency_code, timezone_code = infer_defaults_from_phone(phone_number)
        await complete_existing_user_async(
            cuenta_del_chat, phone_number, currency_code, timezone_code)

        mensaje = (
            "Listo, tu número quedó guardado. ✅\n\n"
            "Ya puedes registrar gastos escribiéndome con normalidad."
        )
        await update.message.reply_text(mensaje, reply_markup=ReplyKeyboardRemove())
        await create_message_async(
            chat, "system", "outgoing", "Teléfono añadido a la cuenta del chat")
        return

    existing_user = await get_user_by_phone_number_async(phone_number)

    if existing_user:
        await update_chat_user_async(chat, existing_user)

        mensaje = (
            f"¡Genial! Tu cuenta quedó vinculada a este chat de Telegram.\n\n"
            f"Moneda por defecto: {existing_user.default_currency}\n\n"
            f"Puedes usar Tresqu en WhatsApp y en Telegram con la misma cuenta."
        )
        await update.message.reply_text(mensaje, reply_markup=ReplyKeyboardRemove())
        await create_message_async(
            chat, "system", "outgoing", "Cuenta existente vinculada al chat")
        return

    # No hay cuenta con ese número: se crea aquí mismo, sin más pasos
    await create_account_from_contact(update, context, chat, tg_user, phone_number)


def setup_bot():
    """Configura la aplicación del bot con todos los manejadores.

    Esta Application se construye de nuevo en cada webhook, así que ningún
    handler puede depender de estado guardado en memoria entre updates: el
    registro se resuelve con el contacto y botones inline que llevan el dato en
    el callback_data.
    """
    # Configurar el token desde settings.py
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Broadcast (admin). Sigue siendo conversacional y arrastra la misma
    # limitación de estado que tenía el registro: pendiente de migrar.
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

    # Comandos básicos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("registrar", register_user))
    application.add_handler(CommandHandler("moneda", currency_command))
    application.add_handler(CommandHandler("timezone", timezone_command))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("ayuda", start))

    # Contacto compartido: crea o vincula la cuenta dentro del mismo update
    application.add_handler(MessageHandler(
        filters.CONTACT & ~filters.COMMAND, handle_contact_shared))

    # Mensajes de voz
    application.add_handler(MessageHandler(
        filters.VOICE, handle_voice_message))

    # Todos los demás mensajes de texto
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message))

    # Callbacks con patrón, siempre ANTES del debug_callback global (que
    # atrapa cualquier cosa y dejaría sordos a los demás).
    from .wallbit_handlers import wallbit_callback_handler
    application.add_handler(
        CallbackQueryHandler(
            wallbit_callback_handler,
            pattern=r"^wallbit_(confirm|cancel)_\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(handle_currency_callback, pattern=r"^cur:"))
    application.add_handler(
        CallbackQueryHandler(handle_timezone_selection, pattern=r"^tz:"))
    application.add_handler(CallbackQueryHandler(debug_callback))

    # Agregar manejador de errores
    application.add_error_handler(error_handler)

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
