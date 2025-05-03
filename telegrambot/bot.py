import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
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


def create_user(external_id, platform, first_name, username):
    return User.objects.create(
        external_id=external_id,
        platform=platform,
        first_name=first_name or "",
        username=username or ""
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
create_user_async = sync_to_async(create_user)
update_chat_user_async = sync_to_async(update_chat_user)
get_chat_user_async = sync_to_async(get_chat_user)


def get_llm_chain():
    """Inicializa la cadena de LangChain de manera perezosa"""
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
        f"Para comenzar, simplemente escribe tu consulta o registra un gasto. "
        f"Para registrarte como usuario, usa el comando /registrar."
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


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registra un nuevo usuario desde Telegram."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    logger.info(
        f"Registrando usuario: {user.id} ({user.username or user.first_name})")

    # Verificar si ya existe un chat
    chat, _ = await get_or_create_chat_async(chat_id)

    # Obtener el usuario asociado al chat de manera segura
    user_id, chat_user = await get_chat_user_async(chat_id)

    # Si el chat no tiene usuario asociado, crear uno
    message = ""
    if user_id is None or chat_user is None:
        # Verificar si ya existe un usuario con este external_id
        external_id = f"telegram_{user.id}"
        existing_user = await get_user_by_external_id_async(external_id)

        if existing_user:
            await update_chat_user_async(chat, existing_user)
            message = "¡Tu cuenta ha sido conectada exitosamente!"
            logger.info(f"Usuario reconectado: {external_id}")
        else:
            # Crear nuevo usuario
            try:
                new_user = await create_user_async(
                    external_id,
                    "telegram",
                    user.first_name,
                    user.username
                )
                await update_chat_user_async(chat, new_user)
                message = "¡Tu cuenta ha sido creada exitosamente!"
                logger.info(f"Nuevo usuario creado: {external_id}")
            except Exception as e:
                logger.error(f"Error al crear usuario: {e}")
                message = "Lo siento, ocurrió un error al crear tu cuenta. Por favor, intenta de nuevo más tarde."
    else:
        message = "Ya tienes una cuenta registrada."
        logger.info(f"Usuario ya registrado: telegram_{user.id}")

    await update.message.reply_text(message)

    # Registrar mensaje y respuesta
    await create_message_async(
        chat,
        str(update.message.message_id),
        "incoming",
        update.message.text
    )

    await create_message_async(
        chat,
        "system",
        "outgoing",
        message
    )


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


def setup_bot():
    """Configura y devuelve la aplicación del bot."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error(
            "¡Token de bot no configurado! Revisa la variable TELEGRAM_BOT_TOKEN")
        raise ValueError("Token de Telegram no configurado")

    logger.info("Inicializando bot de Telegram")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("registrar", register_user))

    # Mensajes no comandos
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot de Telegram inicializado correctamente")
    return application
