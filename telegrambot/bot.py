import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from asgiref.sync import sync_to_async
import time
import tempfile
import os
import asyncio
from django.db import transaction, connections, connection, InterfaceError

from django.conf import settings
from users.models import User
from .models import TelegramChat, TelegramMessage
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
                return TelegramChat.objects.get_or_create(chat_id=str(chat_id))
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
                return TelegramMessage.objects.create(
                    chat=chat,
                    message_id=message_id,
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
    return User.objects.filter(phone_number=phone_number).first()


def create_user(external_id, platform, first_name, username, phone_number=None, default_currency='USD'):
    return User.objects.create(
        external_id=external_id,
        platform=platform,
        first_name=first_name or "",
        username=username or "",
        phone_number=phone_number,
        default_currency=default_currency
    )


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
                chat = TelegramChat.objects.get(chat_id=str(chat_id))
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
        except TelegramChat.DoesNotExist:
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
                chats = TelegramChat.objects.filter(
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envía un mensaje cuando se emite el comando /start."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Guardar el chat en la base de datos si no existe
    chat, created = await get_or_create_chat_async(chat_id)

    welcome_message = (
        f"¡Hola {user.first_name}! Soy CashBot, tu asistente de finanzas personales. "
        f"Puedo ayudarte a registrar gastos y gestionar tu presupuesto.\n\n"
        f"Para comenzar, necesitas registrarte con tu número de teléfono. "
        f"Usa el comando /registrar para iniciar el proceso de registro.\n\n"
        f"Tu número de teléfono nos permite identificarte de manera única "
        f"en todas nuestras plataformas, incluyendo futuras integraciones con WhatsApp."
    )

    await update.message.reply_text(welcome_message)

    # Registrar el mensaje enviado
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        update.message.text
    )

    # Registrar respuesta
    await create_message_async(
        chat,
        "system",
        "outgoing",
        welcome_message
    )


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia el proceso de registro solicitando el número de teléfono."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    logger.info(
        f"Iniciando registro para usuario: {user.id} ({user.username or user.first_name})")

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

    logger.info(f"Mensaje recibido de {chat_id}: {user_message_text[:50]}...")

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Obtener el usuario asociado al chat de manera segura
    user_id, chat_user = await get_chat_user_async(chat_id)
    print(f"😲😲 user_id: {user_id}")
    print(f"👍👍 chat_user: {chat_user.external_id}")

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        user_message_text
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

    # Procesar el mensaje para extraer información y generar embedding
    try:
        # Procesar mensaje
        response = await process_message(chat_user, user_message_text)

        await update.message.reply_text(response, parse_mode="Markdown")

        # Registrar respuesta
        await create_message_async(
            chat,
            "ai_response",
            "outgoing",
            response
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
    chat_id = update.effective_chat.id
    user = update.effective_user
    contact = update.message.contact
    phone_number = contact.phone_number

    # Verificar que el contacto pertenece al usuario que está haciendo el registro
    if str(contact.user_id) != str(user.id):
        await update.message.reply_text(
            "El número de teléfono debe ser el tuyo. Por favor, utiliza el botón para compartir tu contacto.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ESPERANDO_TELEFONO

    logger.info(f"Contacto recibido de {user.id}: {phone_number}")

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        "Contacto compartido"
    )

    # Guardar el número de teléfono en el contexto para usarlo después
    context.user_data["phone_number"] = phone_number

    # Crear botones para monedas comunes
    keyboard = []
    for i in range(0, len(COMMON_CURRENCIES), 2):
        row = []
        currency = COMMON_CURRENCIES[i]
        row.append(InlineKeyboardButton(
            f"{currency['flag']} {currency['code']}",
            callback_data=f"currency_{currency['code']}"
        ))

        # Agregar segunda moneda en la fila si existe
        if i + 1 < len(COMMON_CURRENCIES):
            currency2 = COMMON_CURRENCIES[i + 1]
            row.append(InlineKeyboardButton(
                f"{currency2['flag']} {currency2['code']}",
                callback_data=f"currency_{currency2['code']}"
            ))

        keyboard.append(row)

    # Agregar botón para especificar otra moneda
    keyboard.append([InlineKeyboardButton(
        "Otra moneda", callback_data="currency_other")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    message = (
        "¡Gracias! Ahora necesito que selecciones tu moneda predeterminada. "
        "Esta moneda se usará cuando no especifiques una al registrar gastos.\n\n"
        "Selecciona una de las opciones comunes o elige 'Otra moneda' para escribir el código ISO:"
    )

    await update.message.reply_text(message, reply_markup=reply_markup)

    # Registrar respuesta
    await create_message_async(
        chat,
        "system",
        "outgoing",
        message
    )

    return ESPERANDO_MONEDA


async def handle_currency_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Maneja la selección de moneda desde los botones inline."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    user = update.effective_user

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Extraer el código de moneda del callback_data
    callback_data = query.data

    if callback_data == "currency_other":
        # El usuario quiere especificar otra moneda
        message = (
            "Por favor, escribe el código ISO 4217 de tu moneda (3 letras).\n"
            "Por ejemplo: USD, EUR, GBP, etc."
        )
        await query.edit_message_text(text=message)

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            message
        )

        return ESPERANDO_MONEDA
    else:
        # El usuario ha seleccionado una moneda de la lista
        currency_code = callback_data.replace("currency_", "")

        # Completar el registro con la moneda seleccionada
        return await complete_registration(update, context, currency_code)


async def handle_currency_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Maneja la entrada de texto para el código de moneda."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    currency_code = update.message.text.strip().upper()

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Registrar el mensaje recibido
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        currency_code
    )

    # Validar el código de moneda
    if not is_valid_currency(currency_code):
        message = (
            f"'{currency_code}' no es un código de moneda válido según ISO 4217.\n"
            f"Por favor, escribe un código válido de 3 letras como USD, EUR, GBP, etc."
        )
        await update.message.reply_text(message)

        # Registrar respuesta
        await create_message_async(
            chat,
            "system",
            "outgoing",
            message
        )

        return ESPERANDO_MONEDA

    # Completar el registro con la moneda proporcionada
    return await complete_registration(update, context, currency_code)


async def complete_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, currency_code: str) -> int:
    """Completa el proceso de registro con la información recopilada."""
    if update.callback_query:
        chat_id = update.effective_chat.id
        user = update.effective_user
        message_obj = update.callback_query.message
    else:
        chat_id = update.effective_chat.id
        user = update.effective_user
        message_obj = update.message

    phone_number = context.user_data.get("phone_number")
    external_id = f"telegram_{user.id}"

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Verificar si existe un usuario con este número de teléfono
    existing_user = await get_user_by_phone_number_async(phone_number)

    if existing_user:
        # Si el usuario ya existe, asociar el chat con ese usuario y actualizar moneda
        await update_chat_user_async(chat, existing_user)
        await update_user_currency_async(existing_user, currency_code)
        message = (
            f"¡Tu cuenta ha sido conectada exitosamente!\n"
            f"Has seleccionado {currency_code} ({get_currency_name(currency_code)}) como tu moneda predeterminada."
        )
        logger.info(
            f"Usuario reconectado con moneda {currency_code}: {phone_number}")
    else:
        # Verificar si ya existe un usuario con este external_id
        existing_user_by_id = await get_user_by_external_id_async(external_id)

        if existing_user_by_id:
            # Si existe un usuario con este ID, actualizar su número y moneda
            existing_user_by_id.phone_number = phone_number
            existing_user_by_id.default_currency = currency_code
            await sync_to_async(existing_user_by_id.save)()
            await update_chat_user_async(chat, existing_user_by_id)
            message = (
                f"¡Tu cuenta ha sido actualizada con tu número de teléfono y moneda predeterminada!\n"
                f"Has seleccionado {currency_code} ({get_currency_name(currency_code)}) como tu moneda predeterminada."
            )
            logger.info(
                f"Usuario actualizado con número y moneda {currency_code}: {phone_number}")
        else:
            # Crear nuevo usuario con número de teléfono y moneda
            try:
                new_user = await create_user_async(
                    external_id,
                    "telegram",
                    user.first_name,
                    user.username,
                    phone_number,
                    currency_code
                )
                await update_chat_user_async(chat, new_user)
                message = (
                    f"¡Tu cuenta ha sido creada exitosamente!\n"
                    f"Has seleccionado {currency_code} ({get_currency_name(currency_code)}) como tu moneda predeterminada."
                )
                logger.info(
                    f"Nuevo usuario creado con número y moneda {currency_code}: {phone_number}")
            except Exception as e:
                logger.error(f"Error al crear usuario: {e}")
                message = "Lo siento, ocurrió un error al crear tu cuenta. Por favor, intenta de nuevo más tarde."

    # Enviar mensaje de confirmación
    if update.callback_query:
        await update.callback_query.edit_message_text(message)
    else:
        await message_obj.reply_text(message, reply_markup=ReplyKeyboardRemove())

    # Registrar respuesta
    await create_message_async(
        chat,
        "system",
        "outgoing",
        message
    )

    # Finalizar la conversación
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
                if transcription:
                    await create_message_async(
                        chat,
                        f"transcription_{update.message.message_id}",
                        "incoming",
                        transcription
                    )

                    # Procesar el mensaje con la transcripción
                    response = await process_message(chat_user, transcription)
                else:
                    response = "Lo siento, no pude entender el audio. Por favor, intenta de nuevo con un mensaje de texto o un audio más claro."
            except Exception as e:
                logger.error(f"Error al transcribir audio: {e}")
                response = "Lo siento, hubo un error al procesar tu mensaje de voz. Por favor, intenta de nuevo."

            # Eliminar el mensaje de espera
            await context.bot.delete_message(chat_id=chat_id, message_id=wait_message.message_id)

            # Enviar la respuesta
            await update.message.reply_text(response, parse_mode="Markdown")

            # Registrar respuesta
            await create_message_async(
                chat,
                "ai_response",
                "outgoing",
                response
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
            if str(telegram_chat.chat_id) != str(chat_id):
                await context.bot.send_message(
                    chat_id=telegram_chat.chat_id,
                    text=f"📣 *Mensaje de CashBot*\n\n{broadcast_message}",
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
                f"Error al enviar mensaje a {telegram_chat.chat_id}: {e}")
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


def setup_bot():
    """Configura y devuelve la aplicación del bot."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error(
            "¡Token de bot no configurado! Revisa la variable TELEGRAM_BOT_TOKEN")
        raise ValueError("Token de Telegram no configurado")

    logger.info("Inicializando bot de Telegram")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Comandos básicos
    application.add_handler(CommandHandler("start", start))

    # Manejador de conversación para registro
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("registrar", register_user)],
        states={
            ESPERANDO_TELEFONO: [MessageHandler(filters.CONTACT, handle_contact)],
            ESPERANDO_MONEDA: [
                CallbackQueryHandler(
                    handle_currency_selection, pattern=r"^currency_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               handle_currency_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)

    # Manejador de conversación para broadcast
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", start_broadcast)],
        states={
            ESPERANDO_MENSAJE_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               send_broadcast_message)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(broadcast_handler)

    # Mensajes de voz
    application.add_handler(MessageHandler(
        filters.VOICE, handle_voice_message))

    # Mensajes de texto no comandos
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot de Telegram inicializado correctamente")
    return application
