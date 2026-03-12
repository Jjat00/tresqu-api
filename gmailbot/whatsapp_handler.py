import json
import logging

from django.conf import settings
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from categories.utils import get_or_create_user_expense_category
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
    Verifica si el usuario tiene emails pendientes de categorización.
    Retorna el más antiguo pendiente o None.
    """
    try:
        pending = ProcessedEmail.objects.filter(
            google_account__user=user,
            awaiting_categorization=True,
            processing_status='processed',
        ).order_by('created_at').first()

        return pending

    except Exception as e:
        logger.error(f"Error verificando categorización pendiente para usuario {user.id}: {e}")
        return None


def categorize_gmail_expense(user, processed_email: ProcessedEmail, category_text: str) -> str:
    """
    Usa IA para interpretar el texto de categoría proporcionado por el usuario
    y actualiza el gasto con la categoría correcta.

    Returns:
        str: Mensaje de confirmación para enviar al usuario
    """
    try:
        expense = processed_email.expense
        if not expense:
            processed_email.awaiting_categorization = False
            processed_email.save(update_fields=['awaiting_categorization', 'updated_at'])
            return "No se encontró el gasto asociado a este email. La categorización ha sido cancelada."

        # Obtener las categorías existentes del usuario
        from categories.utils import get_user_expense_categories
        existing_categories = get_user_expense_categories(user)
        categories_str = ', '.join(existing_categories) if existing_categories else 'Ninguna'

        # Usar IA para interpretar la categoría
        prompt = ChatPromptTemplate.from_messages([
            ("system", """Eres un asistente financiero. El usuario quiere categorizar un gasto.
Analiza el texto del usuario y determina la categoría más apropiada.

Categorías existentes del usuario: {existing_categories}

Responde ÚNICAMENTE con un JSON válido (sin markdown, sin backticks):
{{
    "category_name": "Nombre de la categoría",
    "is_new": true/false,
    "description": "Descripción breve si es nueva categoría",
    "examples": "Ejemplos si es nueva categoría",
    "color": "#RRGGBB si es nueva categoría"
}}

REGLAS:
- Si el texto del usuario coincide o es similar a una categoría existente, usa esa
- Si no coincide con ninguna, crea una nueva con is_new=true
- El nombre de la categoría debe estar en el mismo idioma que el usuario
- Prioriza las categorías existentes sobre crear nuevas"""),
            ("human", """El gasto es: {expense_description} por {expense_amount} {expense_currency}
El usuario quiere categorizarlo como: {category_text}"""),
        ])

        chain = prompt | llm
        response = chain.invoke({
            'existing_categories': categories_str,
            'expense_description': expense.description,
            'expense_amount': str(expense.amount),
            'expense_currency': expense.currency,
            'category_text': category_text,
        })

        response_text = response.content.strip()

        # Limpiar posibles backticks
        if response_text.startswith('```'):
            response_text = response_text.strip('`')
            if response_text.startswith('json'):
                response_text = response_text[4:]
            response_text = response_text.strip()

        result = json.loads(response_text)
        category_name = result.get('category_name', category_text.strip().title())

        # Crear o obtener la categoría
        kwargs = {}
        if result.get('is_new'):
            kwargs['description'] = result.get('description', '')
            kwargs['examples'] = result.get('examples', '')
            kwargs['color'] = result.get('color', '')

        category, was_created = get_or_create_user_expense_category(
            user, category_name, **kwargs
        )

        # Actualizar el gasto con la nueva categoría
        expense.user_expense_category = category
        expense.save(update_fields=['user_expense_category', 'updated_at'])

        # Marcar como categorizado
        processed_email.awaiting_categorization = False
        processed_email.save(update_fields=['awaiting_categorization', 'updated_at'])

        action = "creada" if was_created else "asignada"
        confirmation = (
            f"✅ *Gasto categorizado exitosamente*\n\n"
            f"🏪 *{expense.description}* - {expense.amount} {expense.currency}\n"
            f"📁 *Categoría {action}:* {category_name}\n\n"
            f"¡Listo! El gasto ha sido actualizado."
        )

        logger.info(
            f"Gasto {expense.id} categorizado como '{category_name}' "
            f"para usuario {user.id}"
        )
        return confirmation

    except json.JSONDecodeError as e:
        logger.error(f"Error parseando respuesta de categorización: {e}")
        # Fallback: usar el texto del usuario directamente como categoría
        try:
            category, _ = get_or_create_user_expense_category(user, category_text.strip().title())
            expense = processed_email.expense
            if expense:
                expense.user_expense_category = category
                expense.save(update_fields=['user_expense_category', 'updated_at'])
            processed_email.awaiting_categorization = False
            processed_email.save(update_fields=['awaiting_categorization', 'updated_at'])
            return (
                f"✅ Gasto categorizado como *{category_text.strip().title()}*.\n"
                f"¡Listo!"
            )
        except Exception:
            return "Hubo un error categorizando el gasto. Por favor, intenta de nuevo."

    except Exception as e:
        logger.error(f"Error categorizando gasto de Gmail: {e}")
        return "Hubo un error categorizando el gasto. Por favor, intenta de nuevo."
