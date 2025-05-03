import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from asgiref.sync import sync_to_async

from django.conf import settings
from users.models import User
from .models import TelegramChat, TelegramMessage

# Configuración de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Inicializar el modelo de LangChain con OpenAI de manera perezosa
llm = None
chain = None

# Definir estados para la conversación
ESPERANDO_TELEFONO = 0


# Funciones síncronas para operaciones de base de datos
def get_or_create_chat(chat_id):
    return TelegramChat.objects.get_or_create(chat_id=str(chat_id))


def create_message(chat, message_id, message_type, text):
    return TelegramMessage.objects.create(
        chat=chat,
        message_id=message_id,
        message_type=message_type,
        text=text
    )


def get_user_by_external_id(external_id):
    return User.objects.filter(external_id=external_id).first()


def get_user_by_phone_number(phone_number):
    """Busca un usuario por número de teléfono"""
    return User.objects.filter(phone_number=phone_number).first()


def create_user(external_id, platform, first_name, username, phone_number=None):
    return User.objects.create(
        external_id=external_id,
        platform=platform,
        first_name=first_name or "",
        username=username or "",
        phone_number=phone_number
    )


def update_chat_user(chat, user):
    chat.user = user
    chat.save()
    return chat


def get_chat_user(chat_id):
    """Obtiene el usuario asociado a un chat de manera segura"""
    try:
        chat = TelegramChat.objects.get(chat_id=str(chat_id))
        # Hacemos una consulta explícita en lugar de usar chat.user
        # para evitar problemas con el acceso lazy a relaciones
        return chat.user_id, User.objects.filter(id=chat.user_id).first() if chat.user_id else None
    except TelegramChat.DoesNotExist:
        return None, None


# Conversión a funciones asíncronas
get_or_create_chat_async = sync_to_async(get_or_create_chat)
create_message_async = sync_to_async(create_message)
get_user_by_external_id_async = sync_to_async(get_user_by_external_id)
get_user_by_phone_number_async = sync_to_async(get_user_by_phone_number)
create_user_async = sync_to_async(create_user)
update_chat_user_async = sync_to_async(update_chat_user)
get_chat_user_async = sync_to_async(get_chat_user)


def get_llm_chain():
    """Inicializa la cadena de LangChain"""
    global llm, chain

    if not settings.OPENAI_API_KEY:
        logger.error(
            "¡API key de OpenAI no configurada! Revisa la variable OPENAI_API_KEY")
        return None

    if chain is None:
        try:
            logger.info("Inicializando LangChain con OpenAI")
            llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY)
            prompt = ChatPromptTemplate.from_messages([
                ("system", "Eres un asistente útil que ayuda con finanzas personales y administración de gastos."),
                ("user", "{input}")
            ])
            output_parser = StrOutputParser()
            chain = prompt | llm | output_parser
            logger.info("LangChain inicializado correctamente")
        except Exception as e:
            logger.error(f"Error al inicializar LangChain: {e}")
            return None

    return chain


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
    """Maneja mensajes de texto y genera respuestas con LangChain."""
    chat_id = update.effective_chat.id
    user_message_text = update.message.text

    logger.info(f"Mensaje recibido de {chat_id}: {user_message_text[:50]}...")

    # Obtener o crear el chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Obtener el usuario asociado al chat de manera segura
    user_id, chat_user = await get_chat_user_async(chat_id)

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

    # Procesar el mensaje con LangChain y OpenAI
    try:
        # Obtener la cadena de LangChain
        chain = get_llm_chain()

        if not chain:
            raise Exception(
                "No se pudo inicializar LangChain. Verifica la configuración.")

        # Usamos una función síncrona con la invocación de LangChain
        # para evitar problemas con los event loops anidados
        def invoke_chain(text):
            try:
                return chain.invoke({"input": text})
            except Exception as e:
                logger.error(f"Error al invocar LangChain: {e}")
                return f"Lo siento, ocurrió un error al procesar tu mensaje: {str(e)}"

        # Convertimos la función a asíncrona para usarla en este contexto
        invoke_chain_async = sync_to_async(invoke_chain)

        # Procesamos el mensaje de forma asíncrona
        response = await invoke_chain_async(user_message_text)
        logger.info(f"Respuesta generada: {response[:50]}...")

        await update.message.reply_text(response)

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
    """Maneja el contacto recibido y completa el registro del usuario."""
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

    # Verificar si existe un usuario con este número de teléfono
    existing_user = await get_user_by_phone_number_async(phone_number)
    external_id = f"telegram_{user.id}"

    if existing_user:
        # Si el usuario ya existe, asociar el chat con ese usuario
        await update_chat_user_async(chat, existing_user)
        message = "¡Tu cuenta ha sido conectada exitosamente!"
        logger.info(
            f"Usuario reconectado por número de teléfono: {phone_number}")
    else:
        # Verificar si ya existe un usuario con este external_id
        existing_user_by_id = await get_user_by_external_id_async(external_id)

        if existing_user_by_id:
            # Si existe un usuario con este ID pero sin número, actualizar su número
            existing_user_by_id.phone_number = phone_number
            await sync_to_async(existing_user_by_id.save)()
            await update_chat_user_async(chat, existing_user_by_id)
            message = "¡Tu cuenta ha sido actualizada con tu número de teléfono!"
            logger.info(
                f"Usuario actualizado con número de teléfono: {phone_number}")
        else:
            # Crear nuevo usuario con número de teléfono
            try:
                new_user = await create_user_async(
                    external_id,
                    "telegram",
                    user.first_name,
                    user.username,
                    phone_number
                )
                await update_chat_user_async(chat, new_user)
                message = "¡Tu cuenta ha sido creada exitosamente!"
                logger.info(
                    f"Nuevo usuario creado con número de teléfono: {phone_number}")
            except Exception as e:
                logger.error(f"Error al crear usuario: {e}")
                message = "Lo siento, ocurrió un error al crear tu cuenta. Por favor, intenta de nuevo más tarde."

    # Enviar mensaje de confirmación y quitar el teclado
    await update.message.reply_text(message, reply_markup=ReplyKeyboardRemove())

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
        },
        fallbacks=[CommandHandler(
            "cancel", cancel)],
    )
    application.add_handler(conv_handler)

    # Mensajes no comandos
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot de Telegram inicializado correctamente")
    return application
