import json
import logging

from django.conf import settings
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from categories.utils import (
    get_or_create_user_expense_category,
    get_or_create_user_income_category,
)
from .models import ProcessedEmail

logger = logging.getLogger(__name__)

# LLM para interpretar categorías
llm = ChatOpenAI(
    model="gpt-4.1",
    temperature=0.1,
    api_key=settings.OPENAI_API_KEY,
)


def check_pending_categorization(user) -> ProcessedEmail | None:
    """
    Verifica si el usuario tiene emails aún en ventana de categorización/
    corrección. Retorna el MÁS RECIENTE (la respuesta sin swipe-reply
    aplica casi siempre a la última notificación que vio el usuario).
    """
    try:
        pending = ProcessedEmail.objects.filter(
            user=user,
            awaiting_categorization=True,
            processing_status='processed',
        ).order_by('-created_at').first()

        return pending

    except Exception as e:
        logger.error(f"Error verificando categorización pendiente para usuario {user.id}: {e}")
        return None


def find_processed_email_by_notification(
    user, notification_message_id: str
) -> ProcessedEmail | None:
    """
    Busca un ProcessedEmail por el wamid de la notificación de WhatsApp que se
    envió al usuario. Se usa cuando el usuario responde (swipe to reply) a un
    mensaje específico de notificación de compra.

    Incluye tanto los pendientes como los ya categorizados, porque el usuario
    puede querer corregir una auto-categorización.
    """
    if not notification_message_id:
        return None
    try:
        return ProcessedEmail.objects.filter(
            user=user,
            notification_message_id=notification_message_id,
        ).first()
    except Exception as e:
        logger.error(
            f"Error buscando ProcessedEmail por notification_message_id "
            f"'{notification_message_id}' para usuario {user.id}: {e}"
        )
        return None


def categorize_gmail_transaction(
    user, processed_email: ProcessedEmail, category_text: str
) -> str:
    """
    Usa IA para interpretar el texto de categoría proporcionado por el usuario
    y actualiza el gasto O ingreso con la categoría correcta, según lo que
    tenga asociado el ProcessedEmail.

    Returns:
        str: Mensaje de confirmación para enviar al usuario
    """
    is_income = processed_email.income is not None
    txn = processed_email.income if is_income else processed_email.expense
    txn_noun = "ingreso" if is_income else "gasto"
    source_icon = "🏦" if is_income else "🏪"

    try:
        was_already_categorized = not processed_email.awaiting_categorization
        if not txn:
            processed_email.awaiting_categorization = False
            processed_email.save(update_fields=['awaiting_categorization', 'updated_at'])
            return (
                f"No se encontró el {txn_noun} asociado a este email "
                f"(puede que ya haya sido eliminado)."
            )

        # Obtener las categorías existentes del tipo correcto
        if is_income:
            from categories.utils import get_user_income_categories
            existing_categories = get_user_income_categories(user)
        else:
            from categories.utils import get_user_expense_categories
            existing_categories = get_user_expense_categories(user)
        categories_str = ', '.join(existing_categories) if existing_categories else 'Ninguna'

        # Usar IA para interpretar la categoría
        prompt = ChatPromptTemplate.from_messages([
            ("system", """Eres un asistente financiero. El usuario respondió a la notificación de un {txn_noun} detectado automáticamente desde su Gmail. Su respuesta puede ser el nombre de una categoría, una descripción de qué fue la transacción, o algo que no aporta información de categorización.

Categorías existentes del usuario ({txn_noun}): {existing_categories}

Responde ÚNICAMENTE con un JSON válido (sin markdown, sin backticks):
{{
    "is_deletion": true/false,
    "is_categorization": true/false,
    "category_name": "Nombre de la categoría (solo si is_categorization=true)",
    "is_new": true/false,
    "new_description": "Descripción corta de la transacción, o null",
    "description": "Descripción breve si es nueva categoría",
    "examples": "Ejemplos si es nueva categoría",
    "color": "#RRGGBB si es nueva categoría"
}}

REGLAS:
- is_deletion=true si el usuario pide eliminar/borrar/quitar el registro (ej: "elimínalo", "bórralo", "elimina este gasto") O indica que la transacción no es real o no debería registrarse (ej: "no fue una compra", "es un duplicado", "no registres esto"). En ese caso deja los demás campos en false/null.
- is_categorization=true si el texto nombra una categoría (ej: "alimentación", "transporte") O describe qué fue la transacción (ej: "fue la compra de unos cereales" → categoría de alimentación/mercado).
- is_categorization=false si el texto NO permite deducir una categoría: referencias vagas ("me refiero a este", "este gasto"), preguntas, saludos o texto no relacionado. En ese caso deja los demás campos en null.
- Si el usuario DESCRIBIÓ la transacción, además de la categoría devuelve "new_description" con una descripción corta y limpia (ej: "Compra de cereales"). Si solo dio el nombre de una categoría, deja "new_description" en null.
- Si el texto coincide o es similar a una categoría existente, usa esa; solo crea una nueva (is_new=true) si ninguna encaja.
- NUNCA uses "Sin Categorizar" ni "Otros" como category_name: si no puedes deducir algo mejor, devuelve is_categorization=false.
- El nombre de la categoría debe estar en el mismo idioma que el usuario."""),
            ("human", """El {txn_noun} es: {txn_description} por {txn_amount} {txn_currency}
Respuesta del usuario: {category_text}"""),
        ])

        chain = prompt | llm
        response = chain.invoke({
            'txn_noun': txn_noun,
            'existing_categories': categories_str,
            'txn_description': txn.description,
            'txn_amount': str(txn.amount),
            'txn_currency': txn.currency,
            'category_text': category_text,
        })

        response_text = response.content.strip()

        if response_text.startswith('```'):
            response_text = response_text.strip('`')
            if response_text.startswith('json'):
                response_text = response_text[4:]
            response_text = response_text.strip()

        result = json.loads(response_text)

        if result.get('is_deletion'):
            # El usuario pidió eliminar el registro vinculado a esta
            # notificación. El borrado es hard delete: la fila (y su
            # embedding pgvector) desaparecen, así que las consultas
            # semánticas no quedan contaminadas. El FK del ProcessedEmail
            # queda en NULL automáticamente (on_delete=SET_NULL).
            deleted_summary = f"{txn.description} - {txn.amount} {txn.currency}"
            txn.delete()
            # Limpiar también la referencia en memoria: tras el delete la
            # instancia relacionada ya no tiene PK y Django bloquearía el save.
            fk_field = 'income' if is_income else 'expense'
            setattr(processed_email, fk_field, None)
            processed_email.awaiting_categorization = False
            processed_email.save(
                update_fields=[fk_field, 'awaiting_categorization', 'updated_at']
            )
            logger.info(
                f"{txn_noun.capitalize()} eliminado vía respuesta a notificación "
                f"Gmail (ProcessedEmail {processed_email.id}) para usuario {user.id}"
            )
            return (
                f"🗑️ *{txn_noun.capitalize()} eliminado*\n\n"
                f"{source_icon} {deleted_summary}\n\n"
                f"Ya no aparecerá en tus estadísticas ni reportes."
            )

        if not result.get('is_categorization') or not result.get('category_name'):
            # El texto no permite deducir una categoría (ej: "me refiero a este").
            # Dejamos el email en ventana de categorización para que el próximo
            # mensaje del usuario se enrute de vuelta a este flujo, y le pedimos
            # la categoría explícitamente.
            if not processed_email.awaiting_categorization:
                processed_email.awaiting_categorization = True
                processed_email.save(update_fields=['awaiting_categorization', 'updated_at'])
            current_category = (
                txn.user_income_category.name if is_income and txn.user_income_category
                else txn.user_expense_category.name if not is_income and txn.user_expense_category
                else 'Sin Categorizar'
            )
            return (
                f"Este {txn_noun} es:\n\n"
                f"{source_icon} *{txn.description}* - {txn.amount} {txn.currency}\n"
                f"📁 *Categoría actual:* {current_category}\n\n"
                f"¿En qué categoría lo pongo? También puedes describirme qué fue "
                f"(ej: \"fue la compra del mercado\") o pedirme que lo elimine."
            )

        category_name = result.get('category_name')

        kwargs = {}
        if result.get('is_new'):
            kwargs['description'] = result.get('description', '')
            if is_income:
                kwargs['example'] = result.get('examples', '')
            else:
                kwargs['examples'] = result.get('examples', '')
            kwargs['color'] = result.get('color', '')

        new_description = (result.get('new_description') or '').strip()
        txn_update_fields = ['updated_at']
        if new_description:
            txn.description = new_description
            txn_update_fields.append('description')

        # Regenerar el embedding: el original se creó con el comercio y el
        # asunto del correo, pero tras categorizar el movimiento tiene
        # categoría (y a veces nueva descripción), y la búsqueda semántica
        # debe poder encontrarlo por esos términos.
        try:
            from telegrambot.tools import embeddings
            date_value = (
                getattr(txn, 'received_at', None) if is_income
                else getattr(txn, 'spent_at', None)
            )
            embedding_text = (
                f"{txn.description}. {txn_noun.capitalize()} de {txn.amount} "
                f"{txn.currency} en {category_name}"
            )
            if date_value:
                embedding_text += f" el {date_value}"
            txn.embedding = embeddings.embed_query(embedding_text)
            txn_update_fields.append('embedding')
        except Exception as e:
            logger.error(
                f"Error regenerando embedding tras categorizar {txn_noun} "
                f"{txn.id}: {e}"
            )

        if is_income:
            category, was_created = get_or_create_user_income_category(
                user, category_name, **kwargs
            )
            txn.user_income_category = category
            txn.save(update_fields=['user_income_category'] + txn_update_fields)
        else:
            category, was_created = get_or_create_user_expense_category(
                user, category_name, **kwargs
            )
            txn.user_expense_category = category
            txn.save(update_fields=['user_expense_category'] + txn_update_fields)

        processed_email.awaiting_categorization = False
        processed_email.save(update_fields=['awaiting_categorization', 'updated_at'])

        action = "creada" if was_created else "asignada"
        header = (
            f"✅ *{txn_noun.capitalize()} recategorizado*" if was_already_categorized
            else f"✅ *{txn_noun.capitalize()} categorizado exitosamente*"
        )
        confirmation = (
            f"{header}\n\n"
            f"{source_icon} *{txn.description}* - {txn.amount} {txn.currency}\n"
            f"📁 *Categoría {action}:* {category_name}\n\n"
            f"¡Listo! El {txn_noun} ha sido actualizado."
        )

        logger.info(
            f"{txn_noun.capitalize()} {txn.id} categorizado como '{category_name}' "
            f"para usuario {user.id}"
        )
        return confirmation

    except json.JSONDecodeError as e:
        # No adivinamos una categoría a partir del texto crudo: mantenemos el
        # email en ventana de categorización y pedimos la categoría de nuevo.
        logger.error(f"Error parseando respuesta de categorización: {e}")
        try:
            if not processed_email.awaiting_categorization:
                processed_email.awaiting_categorization = True
                processed_email.save(update_fields=['awaiting_categorization', 'updated_at'])
        except Exception:
            pass
        return (
            f"No pude interpretar la categoría para este {txn_noun}. "
            f"Respóndeme con el nombre de la categoría (ej: \"Alimentación\") "
            f"o cuéntame qué fue la compra."
        )

    except Exception as e:
        logger.error(f"Error categorizando {txn_noun} de Gmail: {e}")
        return f"Hubo un error categorizando el {txn_noun}. Por favor, intenta de nuevo."


# Alias retrocompatible (antes solo se manejaban gastos).
categorize_gmail_expense = categorize_gmail_transaction
