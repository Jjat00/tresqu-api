import json
import logging
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from expenses.models import Expense
from categories.utils import get_or_create_user_expense_category
from telegrambot.tools import embeddings

from .models import GoogleAccount, GmailWatch, ProcessedEmail
from .gmail_service import get_gmail_service, get_message, extract_email_text, get_history

logger = logging.getLogger(__name__)

# LLM para análisis de emails de compra
llm = ChatOpenAI(
    model="gpt-4.1",
    temperature=0.1,
    api_key=settings.OPENAI_API_KEY,
)


def parse_purchase_email(email_text: str, subject: str, sender: str) -> dict | None:
    """
    Usa LangChain + GPT-4.1 para analizar si un email es una compra.

    Returns:
        dict con: is_purchase, amount, currency, merchant, date, confidence
        None si hay un error en el procesamiento
    """
    try:
        prompt = ChatPromptTemplate.from_messages([
            ("system", """Eres un experto en análisis de emails financieros. Tu tarea es determinar si un email
corresponde a una compra, pago o transacción financiera, y extraer los detalles relevantes.

Analiza el contenido del email y responde ÚNICAMENTE con un JSON válido (sin markdown, sin backticks),
con la siguiente estructura:

{{
    "is_purchase": true/false,
    "amount": 0.0,
    "currency": "USD",
    "merchant": "Nombre del comercio",
    "date": "YYYY-MM-DD",
    "confidence": 0.0
}}

REGLAS:
- is_purchase: true si el email es una confirmación de compra, recibo, factura o notificación de pago
- is_purchase: false si es publicidad, promoción, newsletter, notificación sin transacción real, o cualquier otro tipo de email
- amount: monto numérico de la transacción (sin símbolos de moneda). Si hay múltiples montos, usar el total
- currency: código ISO 4217 de la moneda (USD, EUR, COP, MXN, etc.)
- merchant: nombre del comercio o empresa que envió el recibo
- date: fecha de la transacción en formato YYYY-MM-DD. Si no se encuentra, usar null
- confidence: nivel de confianza de 0.0 a 1.0 sobre si es una compra real

IMPORTANTE:
- Solo marca is_purchase=true si estás seguro de que es una transacción real, no publicidad
- Si el email es una alerta de cargo bancario o notificación de pago, es una compra
- Si es un email de confirmación de pedido con monto, es una compra
- Los emails de suscripción (Netflix, Spotify, etc.) son compras
- Los emails de envío sin monto NO son compras
- Los newsletters y promociones NO son compras"""),
            ("human", """Analiza este email:

ASUNTO: {subject}
REMITENTE: {sender}
CONTENIDO:
{body}"""),
        ])

        chain = prompt | llm
        response = chain.invoke({
            'subject': subject,
            'sender': sender,
            'body': email_text,
        })

        # Parsear la respuesta JSON
        response_text = response.content.strip()

        # Limpiar posibles backticks de markdown
        if response_text.startswith('```'):
            response_text = response_text.strip('`')
            if response_text.startswith('json'):
                response_text = response_text[4:]
            response_text = response_text.strip()

        result = json.loads(response_text)
        logger.info(f"Análisis de email completado: is_purchase={result.get('is_purchase')}, confidence={result.get('confidence')}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Error parseando respuesta JSON del LLM: {e}, respuesta: {response_text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Error analizando email de compra: {e}")
        return None


def process_email_for_user(google_account, gmail_message_id):
    """
    Pipeline completo de procesamiento de un email individual.
    1. Verifica deduplicación
    2. Obtiene el mensaje de Gmail
    3. Analiza con IA si es compra
    4. Crea el gasto si es compra
    5. Envía notificación por WhatsApp
    """
    try:
        # 1. Verificar deduplicación
        if ProcessedEmail.objects.filter(
            google_account=google_account,
            gmail_message_id=gmail_message_id
        ).exists():
            logger.info(f"Email {gmail_message_id} ya fue procesado, omitiendo")
            return

        # 2. Obtener el mensaje de Gmail
        service = get_gmail_service(google_account)
        message = get_message(service, gmail_message_id)
        email_data = extract_email_text(message)

        subject = email_data.get('subject', '')
        sender = email_data.get('sender', '')
        body = email_data.get('body', '')
        received_at = email_data.get('date')

        # 3. Crear registro de email procesado (estado pending)
        processed_email = ProcessedEmail.objects.create(
            google_account=google_account,
            gmail_message_id=gmail_message_id,
            subject=subject[:500],
            sender=sender[:255],
            received_at=received_at,
            processing_status='pending',
        )

        # 4. Analizar con IA si es un email de compra
        ai_result = parse_purchase_email(body, subject, sender)

        if ai_result is None:
            processed_email.processing_status = 'error'
            processed_email.ai_response = 'Error en el análisis de IA'
            processed_email.save(update_fields=['processing_status', 'ai_response', 'updated_at'])
            return

        processed_email.ai_response = json.dumps(ai_result, ensure_ascii=False)

        # 5. Si no es compra, marcar como omitido
        if not ai_result.get('is_purchase', False) or ai_result.get('confidence', 0) < 0.6:
            processed_email.processing_status = 'skipped'
            processed_email.is_purchase = False
            processed_email.save(update_fields=[
                'processing_status', 'is_purchase', 'ai_response', 'updated_at'
            ])
            logger.info(f"Email {gmail_message_id} no es una compra (confidence={ai_result.get('confidence', 0)})")
            return

        # 6. Verificar límites del plan del usuario
        user = google_account.user
        can_add, limit_message = user.can_add_expense()
        if not can_add:
            processed_email.processing_status = 'skipped'
            processed_email.is_purchase = True
            processed_email.ai_response = json.dumps({
                **ai_result,
                'skipped_reason': limit_message,
            }, ensure_ascii=False)
            processed_email.save(update_fields=[
                'processing_status', 'is_purchase', 'ai_response', 'updated_at'
            ])
            logger.warning(f"Límite de gastos alcanzado para usuario {user.id}: {limit_message}")
            return

        # 7. Crear el gasto
        merchant = ai_result.get('merchant', 'Compra por email')
        amount = ai_result.get('amount', 0)
        currency = ai_result.get('currency', user.default_currency)
        purchase_date_str = ai_result.get('date')

        # Parsear la fecha de la compra
        spent_at = None
        if purchase_date_str:
            try:
                spent_at = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                spent_at = timezone.now().date()
        else:
            spent_at = timezone.now().date()

        # Categoría por defecto: "Sin Categorizar"
        category, _ = get_or_create_user_expense_category(
            user,
            'Sin Categorizar',
            description='Gastos detectados por Gmail pendientes de categorización',
            examples='Compras por email, pagos detectados automáticamente',
        )

        # Generar embedding para el gasto
        embedding_text = f"{merchant} {subject}"
        embedding = None
        try:
            embedding = embeddings.embed_query(embedding_text)
        except Exception as e:
            logger.error(f"Error generando embedding para gasto de Gmail: {e}")

        # Crear el gasto en la base de datos
        expense = Expense.objects.create(
            user=user,
            amount=Decimal(str(amount)),
            currency=currency,
            description=merchant,
            timestamp=timezone.now(),
            spent_at=spent_at,
            note=f"Detectado automáticamente desde Gmail: {subject[:200]}",
            raw_message=f"[Gmail] {subject}",
            user_expense_category=category,
            embedding=embedding,
        )

        # Incrementar contador de uso mensual
        try:
            monthly_usage = user.get_current_monthly_usage()
            monthly_usage.increment_expenses()
        except Exception as e:
            logger.error(f"Error incrementando uso mensual: {e}")

        # 8. Actualizar el email procesado
        processed_email.is_purchase = True
        processed_email.expense = expense
        processed_email.processing_status = 'processed'
        processed_email.awaiting_categorization = True
        processed_email.save(update_fields=[
            'is_purchase', 'expense', 'processing_status',
            'awaiting_categorization', 'ai_response', 'updated_at'
        ])

        logger.info(
            f"Gasto creado desde Gmail para usuario {user.id}: "
            f"{amount} {currency} - {merchant}"
        )

        # 9. Enviar notificación por WhatsApp
        try:
            send_purchase_confirmation_whatsapp(user, expense, merchant, amount, currency)
        except Exception as e:
            logger.error(f"Error enviando notificación WhatsApp: {e}")

    except Exception as e:
        logger.error(f"Error procesando email {gmail_message_id}: {e}")
        # Intentar actualizar el estado del email si ya fue creado
        try:
            ProcessedEmail.objects.filter(
                google_account=google_account,
                gmail_message_id=gmail_message_id
            ).update(processing_status='error', ai_response=str(e))
        except Exception:
            pass


def process_history_update(google_account, new_history_id):
    """
    Procesa todos los mensajes nuevos desde el último history_id conocido.
    Actualiza el history_id del watch al finalizar.
    """
    try:
        watch = google_account.watch

        if not watch or not watch.history_id:
            logger.warning(f"No hay history_id para {google_account.google_email}")
            return

        start_history_id = watch.history_id

        # Obtener los IDs de mensajes nuevos
        message_ids = get_history(google_account, start_history_id)

        if not message_ids:
            logger.info(f"No hay mensajes nuevos para {google_account.google_email}")
        else:
            logger.info(
                f"Procesando {len(message_ids)} mensajes nuevos para "
                f"{google_account.google_email}"
            )

            # Procesar cada mensaje
            for msg_id in message_ids:
                try:
                    process_email_for_user(google_account, msg_id)
                except Exception as e:
                    logger.error(f"Error procesando mensaje {msg_id}: {e}")
                    continue

        # Actualizar el history_id del watch
        if new_history_id:
            watch.history_id = new_history_id
            watch.save(update_fields=['history_id', 'updated_at'])

    except Exception as e:
        logger.error(f"Error procesando actualización de historial para {google_account.google_email}: {e}")


def send_purchase_confirmation_whatsapp(user, expense, merchant, amount, currency):
    """
    Envía un mensaje de WhatsApp al usuario informando sobre una compra detectada
    y solicitando la categorización.
    """
    try:
        phone_number = user.phone_number
        if not phone_number:
            logger.info(f"Usuario {user.id} no tiene número de teléfono registrado")
            return

        from whatsappbot.views import send_meta_whatsapp_message

        message = (
            f"📧 *Compra detectada desde tu Gmail:*\n\n"
            f"🏪 *Comercio:* {merchant}\n"
            f"💰 *Monto:* {amount} {currency}\n"
            f"📅 *Fecha:* {expense.spent_at}\n\n"
            f"He registrado este gasto en la categoría *Sin Categorizar*.\n"
            f"Responde con el nombre de la categoría para clasificarlo "
            f"(ej: Alimentación, Transporte, Entretenimiento, etc.)"
        )

        success = send_meta_whatsapp_message(phone_number, message)
        if success:
            logger.info(f"Notificación de compra enviada a {phone_number}")
        else:
            logger.error(f"Error enviando notificación de compra a {phone_number}")

    except ImportError:
        logger.warning("whatsappbot no disponible para enviar notificaciones")
    except Exception as e:
        logger.error(f"Error enviando confirmación de compra por WhatsApp: {e}")
