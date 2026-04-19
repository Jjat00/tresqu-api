import json
import logging
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from expenses.models import Expense
from categories.utils import (
    get_or_create_user_expense_category,
    get_user_categories_with_details,
)
from telegrambot.tools import embeddings

from .models import GoogleAccount, GmailWatch, ProcessedEmail
from .gmail_service import get_gmail_service, get_message, extract_email_text, get_history

# Umbral de confianza: por encima de esto auto-asignamos la categoría
# sugerida por el LLM sin pedirle al usuario que clasifique.
AUTO_CATEGORIZATION_CONFIDENCE_THRESHOLD = 0.8

logger = logging.getLogger(__name__)

# LLM para análisis de emails de compra
llm = ChatOpenAI(
    model="gpt-4.1",
    temperature=0.1,
    api_key=settings.OPENAI_API_KEY,
)


def parse_purchase_email(
    email_text: str,
    subject: str,
    sender: str,
    user_categories: list[dict] | None = None,
) -> dict | None:
    """
    Usa LangChain + GPT-4.1 para analizar si un email es una compra
    y sugerir una categoría dentro de las del usuario cuando sea posible.

    Returns:
        dict con: is_purchase, amount, currency, merchant, date, confidence,
        suggested_category, category_confidence
        None si hay un error en el procesamiento
    """
    try:
        if user_categories:
            categories_block = "\n".join([
                f"- {c['name']}"
                + (f" — {c['description']}" if c.get('description') else '')
                + (f" (ej: {c['examples']})" if c.get('examples') else '')
                for c in user_categories
            ])
        else:
            categories_block = "(sin categorías propias)"

        prompt = ChatPromptTemplate.from_messages([
            ("system", """Eres un experto en análisis de emails financieros. Tu tarea es determinar si un email
corresponde a una compra, pago o transacción financiera, extraer los detalles relevantes
y sugerir la categoría de gasto más adecuada dentro de las categorías del usuario.

CATEGORÍAS DEL USUARIO:
{categories_block}

Analiza el contenido del email y responde ÚNICAMENTE con un JSON válido (sin markdown, sin backticks),
con la siguiente estructura:

{{{{
    "is_purchase": true/false,
    "payment_status": "success" | "failed" | "unknown",
    "amount": 0.0,
    "currency": "USD",
    "merchant": "Nombre del comercio",
    "date": "YYYY-MM-DD",
    "confidence": 0.0,
    "suggested_category": "Nombre exacto de una categoría existente o null",
    "category_confidence": 0.0
}}}}

REGLAS:
- is_purchase: true SOLO si el email describe una transacción EXITOSA y completada
  (compra confirmada, recibo, factura, cargo procesado, pago aprobado).
- is_purchase: false si es:
    * publicidad, promoción, newsletter, notificación sin transacción real
    * un INTENTO de pago RECHAZADO, DECLINADO, FALLIDO o NO AUTORIZADO
      (p. ej. "pago rechazado", "transacción declinada", "no se pudo procesar",
      "fondos insuficientes", "operación fallida", "intento de pago rechazado",
      "declined", "failed", "rejected", "unsuccessful", "tarjeta rechazada")
    * una alerta de seguridad sin cobro real
    * un email de envío/entrega sin monto
- payment_status:
    * "success" si el pago / compra se procesó exitosamente
    * "failed" si fue rechazado, declinado, sin fondos, no autorizado, fallido
    * "unknown" si el email no deja claro el resultado
- amount: monto numérico de la transacción (sin símbolos de moneda). Si hay múltiples montos, usar el total.
- currency: código ISO 4217 de la moneda (USD, EUR, COP, MXN, etc.)
- merchant: nombre del comercio o empresa que envió el recibo
- date: fecha de la transacción en formato YYYY-MM-DD. Si no se encuentra, usar null
- confidence: nivel de confianza de 0.0 a 1.0 sobre si es una compra real Y exitosa
- suggested_category: DEBE ser EXACTAMENTE uno de los nombres de la lista anterior.
  Si ninguna encaja bien, usar null.
- category_confidence: 0.0 a 1.0 indicando qué tan seguro estás de la categoría.
  Usa >=0.8 solo si el comercio y el contexto hacen la categoría obvia
  (ej: Uber → Transporte, Netflix → Suscripciones, Rappi → Alimentación).
  Usa <0.6 si estás adivinando.

IMPORTANTE:
- Si payment_status="failed", OBLIGATORIO is_purchase=false (aunque haya monto y comercio).
- Los emails de suscripción renovada exitosamente (Netflix, Spotify, etc.) son compras.
- Los emails de envío sin monto NO son compras.
- Los newsletters y promociones NO son compras.
- NO inventes categorías nuevas: solo elige una existente o devuelve null."""),
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
            'categories_block': categories_block,
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
        logger.info(
            f"Análisis de email completado: is_purchase={result.get('is_purchase')}, "
            f"confidence={result.get('confidence')}, "
            f"suggested_category={result.get('suggested_category')}, "
            f"category_confidence={result.get('category_confidence')}"
        )
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

        # 4. Analizar con IA si es un email de compra, pasándole las categorías
        # del usuario para que intente sugerir una.
        user = google_account.user
        user_categories = []
        try:
            categories_detail = get_user_categories_with_details(user)
            user_categories = categories_detail.get('expense_categories', [])
        except Exception as e:
            logger.error(f"Error obteniendo categorías para sugerencia: {e}")

        ai_result = parse_purchase_email(body, subject, sender, user_categories=user_categories)

        if ai_result is None:
            processed_email.processing_status = 'error'
            processed_email.ai_response = 'Error en el análisis de IA'
            processed_email.save(update_fields=['processing_status', 'ai_response', 'updated_at'])
            return

        processed_email.ai_response = json.dumps(ai_result, ensure_ascii=False)

        # 5. Si no es compra exitosa, marcar como omitido. Cubre:
        #    - is_purchase=false
        #    - confidence baja
        #    - payment_status="failed" (pagos rechazados / declinados / fallidos)
        payment_status = (ai_result.get('payment_status') or 'unknown').lower()
        is_purchase = ai_result.get('is_purchase', False)
        confidence = ai_result.get('confidence', 0)
        if payment_status == 'failed' or not is_purchase or confidence < 0.6:
            processed_email.processing_status = 'skipped'
            processed_email.is_purchase = False
            processed_email.save(update_fields=[
                'processing_status', 'is_purchase', 'ai_response', 'updated_at'
            ])
            logger.info(
                f"Email {gmail_message_id} omitido "
                f"(is_purchase={is_purchase}, confidence={confidence}, "
                f"payment_status={payment_status})"
            )
            return

        # 6. Verificar límites del plan del usuario
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

        # Decidir categoría: auto-asignar si el LLM sugirió una existente
        # con alta confianza; si no, dejar "Sin Categorizar" y pedir al usuario.
        suggested_category_name = ai_result.get('suggested_category')
        category_confidence = float(ai_result.get('category_confidence') or 0.0)
        existing_category_names_lower = {
            c['name'].lower() for c in user_categories
        }
        auto_categorized = (
            bool(suggested_category_name)
            and suggested_category_name.lower() in existing_category_names_lower
            and category_confidence >= AUTO_CATEGORIZATION_CONFIDENCE_THRESHOLD
        )

        if auto_categorized:
            # Usar la categoría existente tal cual el LLM la nombró
            category, _ = get_or_create_user_expense_category(
                user, suggested_category_name
            )
            logger.info(
                f"Auto-categorizando gasto como '{suggested_category_name}' "
                f"(confianza={category_confidence}) para usuario {user.id}"
            )
        else:
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
        # Dejamos awaiting_categorization=True incluso cuando se auto-categorizó,
        # para que el usuario pueda corregir la categoría respondiendo con texto
        # plano (no solo con swipe-reply). El intent-classifier en el bot filtra
        # falsos positivos (un "gasté 20k en pan" no se toma como categoría).
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

        # 9. Enviar notificación por WhatsApp y guardar el wamid para resolver
        # respuestas del usuario con "quote" (swipe to reply).
        try:
            sent_message_id = send_purchase_confirmation_whatsapp(
                user,
                expense,
                merchant,
                amount,
                currency,
                auto_categorized=auto_categorized,
                category_name=category.name,
            )
            if sent_message_id:
                processed_email.notification_message_id = sent_message_id
                processed_email.save(update_fields=[
                    'notification_message_id', 'updated_at'
                ])
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


def send_purchase_confirmation_whatsapp(
    user,
    expense,
    merchant,
    amount,
    currency,
    auto_categorized: bool = False,
    category_name: str = 'Sin Categorizar',
) -> str | None:
    """
    Envía un mensaje de WhatsApp al usuario informando sobre una compra detectada.
    Si auto_categorized=True, solo informa (categoría ya asignada).
    Si no, solicita la categorización al usuario.

    Returns:
        El wamid del mensaje enviado (para tracking de respuestas) o None si falla.
    """
    try:
        phone_number = user.phone_number
        if not phone_number:
            logger.info(f"Usuario {user.id} no tiene número de teléfono registrado")
            return None

        from whatsappbot.views import send_meta_whatsapp_message

        if auto_categorized:
            message = (
                f"📧 *Compra detectada desde tu Gmail:*\n\n"
                f"🏪 *Comercio:* {merchant}\n"
                f"💰 *Monto:* {amount} {currency}\n"
                f"📅 *Fecha:* {expense.spent_at}\n"
                f"📁 *Categoría:* {category_name}\n\n"
                f"Registrado automáticamente. Si la categoría no es correcta, "
                f"respóndeme con la categoría correcta (ej: Ocio, Transporte) "
                f"o desliza este mensaje para responderlo directamente."
            )
        else:
            message = (
                f"📧 *Compra detectada desde tu Gmail:*\n\n"
                f"🏪 *Comercio:* {merchant}\n"
                f"💰 *Monto:* {amount} {currency}\n"
                f"📅 *Fecha:* {expense.spent_at}\n\n"
                f"He registrado este gasto en la categoría *Sin Categorizar*.\n"
                f"Responde con el nombre de la categoría para clasificarlo "
                f"(ej: Alimentación, Transporte, Entretenimiento, etc.)"
            )

        success, sent_message_id = send_meta_whatsapp_message(
            phone_number, message, return_message_id=True
        )
        if success:
            logger.info(
                f"Notificación de compra enviada a {phone_number} "
                f"(wamid={sent_message_id})"
            )
            return sent_message_id
        else:
            logger.error(f"Error enviando notificación de compra a {phone_number}")
            return None

    except ImportError:
        logger.warning("whatsappbot no disponible para enviar notificaciones")
        return None
    except Exception as e:
        logger.error(f"Error enviando confirmación de compra por WhatsApp: {e}")
        return None
