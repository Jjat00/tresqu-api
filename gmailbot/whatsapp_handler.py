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
            google_account__user=user,
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
            google_account__user=user,
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
                f"No se encontró el {txn_noun} asociado a este email. "
                f"La categorización ha sido cancelada."
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
            ("system", """Eres un asistente financiero. El usuario quiere categorizar un {txn_noun}.
Analiza el texto del usuario y determina la categoría más apropiada.

Categorías existentes del usuario ({txn_noun}): {existing_categories}

Responde ÚNICAMENTE con un JSON válido (sin markdown, sin backticks):
{{{{
    "category_name": "Nombre de la categoría",
    "is_new": true/false,
    "description": "Descripción breve si es nueva categoría",
    "examples": "Ejemplos si es nueva categoría",
    "color": "#RRGGBB si es nueva categoría"
}}}}

REGLAS:
- Si el texto del usuario coincide o es similar a una categoría existente, usa esa
- Si no coincide con ninguna, crea una nueva con is_new=true
- El nombre de la categoría debe estar en el mismo idioma que el usuario
- Prioriza las categorías existentes sobre crear nuevas"""),
            ("human", """El {txn_noun} es: {txn_description} por {txn_amount} {txn_currency}
El usuario quiere categorizarlo como: {category_text}"""),
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
        category_name = result.get('category_name', category_text.strip().title())

        kwargs = {}
        if result.get('is_new'):
            kwargs['description'] = result.get('description', '')
            if is_income:
                kwargs['example'] = result.get('examples', '')
            else:
                kwargs['examples'] = result.get('examples', '')
            kwargs['color'] = result.get('color', '')

        if is_income:
            category, was_created = get_or_create_user_income_category(
                user, category_name, **kwargs
            )
            txn.user_income_category = category
            txn.save(update_fields=['user_income_category', 'updated_at'])
        else:
            category, was_created = get_or_create_user_expense_category(
                user, category_name, **kwargs
            )
            txn.user_expense_category = category
            txn.save(update_fields=['user_expense_category', 'updated_at'])

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
        logger.error(f"Error parseando respuesta de categorización: {e}")
        try:
            if is_income:
                category, _ = get_or_create_user_income_category(
                    user, category_text.strip().title()
                )
                if txn:
                    txn.user_income_category = category
                    txn.save(update_fields=['user_income_category', 'updated_at'])
            else:
                category, _ = get_or_create_user_expense_category(
                    user, category_text.strip().title()
                )
                if txn:
                    txn.user_expense_category = category
                    txn.save(update_fields=['user_expense_category', 'updated_at'])
            processed_email.awaiting_categorization = False
            processed_email.save(update_fields=['awaiting_categorization', 'updated_at'])
            return (
                f"✅ {txn_noun.capitalize()} categorizado como "
                f"*{category_text.strip().title()}*.\n¡Listo!"
            )
        except Exception:
            return f"Hubo un error categorizando el {txn_noun}. Por favor, intenta de nuevo."

    except Exception as e:
        logger.error(f"Error categorizando {txn_noun} de Gmail: {e}")
        return f"Hubo un error categorizando el {txn_noun}. Por favor, intenta de nuevo."


# Alias retrocompatible (antes solo se manejaban gastos).
categorize_gmail_expense = categorize_gmail_transaction
