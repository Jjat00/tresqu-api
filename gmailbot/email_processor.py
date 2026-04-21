import json
import logging
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from expenses.models import Expense
from income.models import Income
from categories.utils import (
    get_or_create_user_expense_category,
    get_or_create_user_income_category,
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
    user_expense_categories: list[dict] | None = None,
    user_income_categories: list[dict] | None = None,
) -> dict | None:
    """
    Usa LangChain + GPT-4.1 para analizar un email financiero, determinar si
    corresponde a una transacción real, clasificar entre gasto e ingreso, y
    sugerir una categoría dentro de las del usuario cuando sea posible.

    Returns:
        dict con: is_purchase, transaction_type ("expense"|"income"|"none"),
        payment_status, amount, currency, merchant, date, confidence,
        suggested_category, category_confidence
        None si hay un error en el procesamiento
    """
    try:
        def _fmt_block(cats):
            if not cats:
                return "(sin categorías propias)"
            return "\n".join([
                f"- {c['name']}"
                + (f" — {c['description']}" if c.get('description') else '')
                + (f" (ej: {c.get('examples') or c.get('example') or ''})"
                   if (c.get('examples') or c.get('example')) else '')
                for c in cats
            ])

        expense_block = _fmt_block(user_expense_categories)
        income_block = _fmt_block(user_income_categories)

        prompt = ChatPromptTemplate.from_messages([
            ("system", """Eres un experto en análisis de emails financieros. Tu tarea es
determinar si un email corresponde a una transacción real (GASTO o INGRESO),
extraer sus detalles y sugerir una categoría dentro de las del usuario.

CATEGORÍAS DE GASTO DEL USUARIO:
{expense_block}

CATEGORÍAS DE INGRESO DEL USUARIO:
{income_block}

Responde ÚNICAMENTE con un JSON válido (sin markdown, sin backticks):

{{{{
    "is_purchase": true/false,
    "transaction_type": "expense" | "income" | "none",
    "payment_status": "success" | "failed" | "unknown",
    "amount": 0.0,
    "currency": "USD",
    "merchant": "Nombre del comercio o fuente del ingreso",
    "date": "YYYY-MM-DD",
    "confidence": 0.0,
    "suggested_category": "Nombre exacto de una categoría existente o null",
    "category_confidence": 0.0
}}}}

TIPO DE TRANSACCIÓN (lo más importante — no te equivoques):
- "expense": el usuario PAGÓ / compró / se le hizo un cargo / renovó suscripción.
  Señales: "compra", "cargo", "pago realizado", "factura", "recibo", "renovación",
  "se realizó un pago", "charged", "payment successful", "receipt", "your order".
- "income": el usuario RECIBIÓ dinero. Señales: "te depositaron", "te transfirieron",
  "recibiste", "abono a tu cuenta", "giro recibido", "consignación", "nómina",
  "payment received", "you received", "deposit", "transfer received", "payout".
- "none": no es transacción real (publicidad, alerta de seguridad, envío sin cobro,
  newsletter, etc.) O el pago/giro fue RECHAZADO/FALLIDO.

REGLAS:
- is_purchase: true solo si transaction_type != "none" Y payment_status="success".
  (Mantenido por compatibilidad con código existente; no intentes re-definir.)
- payment_status:
    * "success" si la transacción se completó (cargo aprobado / depósito acreditado).
    * "failed" si fue rechazada, declinada, sin fondos, no autorizada, reversada.
    * "unknown" si el email no deja claro el resultado.
- Si payment_status="failed" → transaction_type="none" y is_purchase=false.
- amount: monto numérico total (sin símbolos).
- currency: código ISO 4217 (USD, EUR, COP, MXN...).
- merchant:
    * Para expense: nombre del comercio o empresa (Rappi, Uber, Netflix, banco).
    * Para income: fuente del ingreso (empresa empleadora, remitente, plataforma).
- date: fecha YYYY-MM-DD o null.
- confidence: 0.0 a 1.0 sobre si es transacción real Y exitosa.
- suggested_category: EXACTAMENTE uno de los nombres de la lista del tipo detectado
  (gasto si transaction_type="expense", ingreso si "income"). Si ninguna encaja,
  null. NO mezcles: no sugieras una categoría de gasto para un ingreso o viceversa.
- category_confidence: 0.0 a 1.0. Usa >=0.8 solo si el contexto hace la categoría
  obvia (ej. empleador → Salario, Uber → Transporte, Rappi → Alimentación).

IMPORTANTE:
- Los emails de suscripción renovada exitosamente son gastos (expense).
- Un "giro recibido" / "te depositaron" / "abono a tu cuenta" es income.
- Los emails de envío sin monto NO son transacciones.
- Los newsletters y promociones NO son transacciones.
- NO inventes categorías nuevas: elige una existente del set correcto o devuelve null."""),
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
            'expense_block': expense_block,
            'income_block': income_block,
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
            f"Análisis de email completado: "
            f"transaction_type={result.get('transaction_type')}, "
            f"is_purchase={result.get('is_purchase')}, "
            f"payment_status={result.get('payment_status')}, "
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

        # 4. Analizar con IA si es una transacción real (gasto o ingreso),
        # pasándole ambos sets de categorías del usuario para sugerencia.
        user = google_account.user
        user_expense_categories = []
        user_income_categories = []
        try:
            categories_detail = get_user_categories_with_details(user)
            user_expense_categories = categories_detail.get('expense_categories', [])
            user_income_categories = categories_detail.get('income_categories', [])
        except Exception as e:
            logger.error(f"Error obteniendo categorías para sugerencia: {e}")

        ai_result = parse_purchase_email(
            body,
            subject,
            sender,
            user_expense_categories=user_expense_categories,
            user_income_categories=user_income_categories,
        )

        if ai_result is None:
            processed_email.processing_status = 'error'
            processed_email.ai_response = 'Error en el análisis de IA'
            processed_email.save(update_fields=['processing_status', 'ai_response', 'updated_at'])
            return

        processed_email.ai_response = json.dumps(ai_result, ensure_ascii=False)

        # 5. Si no es transacción exitosa, marcar como omitido. Cubre:
        #    - transaction_type="none"
        #    - confidence baja
        #    - payment_status="failed" (pagos rechazados / declinados / fallidos)
        payment_status = (ai_result.get('payment_status') or 'unknown').lower()
        transaction_type = (ai_result.get('transaction_type') or '').lower()
        is_purchase = ai_result.get('is_purchase', False)
        confidence = ai_result.get('confidence', 0)
        is_valid_txn = transaction_type in ('expense', 'income') and is_purchase
        if payment_status == 'failed' or not is_valid_txn or confidence < 0.6:
            processed_email.processing_status = 'skipped'
            processed_email.is_purchase = False
            processed_email.transaction_type = transaction_type or ''
            processed_email.save(update_fields=[
                'processing_status', 'is_purchase', 'transaction_type',
                'ai_response', 'updated_at'
            ])
            logger.info(
                f"Email {gmail_message_id} omitido "
                f"(transaction_type={transaction_type}, is_purchase={is_purchase}, "
                f"confidence={confidence}, payment_status={payment_status})"
            )
            return

        is_income = transaction_type == 'income'

        # 6. Verificar límites del plan del usuario (rama según tipo)
        if is_income:
            can_add, limit_message = user.can_add_income()
        else:
            can_add, limit_message = user.can_add_expense()
        if not can_add:
            processed_email.processing_status = 'skipped'
            processed_email.is_purchase = True
            processed_email.transaction_type = transaction_type
            processed_email.ai_response = json.dumps({
                **ai_result,
                'skipped_reason': limit_message,
            }, ensure_ascii=False)
            processed_email.save(update_fields=[
                'processing_status', 'is_purchase', 'transaction_type',
                'ai_response', 'updated_at'
            ])
            logger.warning(
                f"Límite de {transaction_type}s alcanzado para usuario {user.id}: "
                f"{limit_message}"
            )
            return

        # 7. Resolver datos comunes
        merchant = ai_result.get('merchant') or (
            'Ingreso por email' if is_income else 'Compra por email'
        )
        amount = ai_result.get('amount', 0)
        currency = ai_result.get('currency', user.default_currency)
        date_str = ai_result.get('date')
        txn_date = None
        if date_str:
            try:
                txn_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                txn_date = timezone.now().date()
        else:
            txn_date = timezone.now().date()

        # 8. Decidir categoría (auto-asignar si confianza alta y existe en el
        # set del tipo correcto; si no, "Sin Categorizar" y pedir al usuario).
        suggested_category_name = ai_result.get('suggested_category')
        category_confidence = float(ai_result.get('category_confidence') or 0.0)
        relevant_categories = (
            user_income_categories if is_income else user_expense_categories
        )
        existing_category_names_lower = {
            c['name'].lower() for c in relevant_categories
        }
        auto_categorized = (
            bool(suggested_category_name)
            and suggested_category_name.lower() in existing_category_names_lower
            and category_confidence >= AUTO_CATEGORIZATION_CONFIDENCE_THRESHOLD
        )

        if is_income:
            if auto_categorized:
                category, _ = get_or_create_user_income_category(
                    user, suggested_category_name
                )
                logger.info(
                    f"Auto-categorizando ingreso como '{suggested_category_name}' "
                    f"(confianza={category_confidence}) para usuario {user.id}"
                )
            else:
                category, _ = get_or_create_user_income_category(
                    user,
                    'Sin Categorizar',
                    description='Ingresos detectados por Gmail pendientes de categorización',
                    example='Depósitos, transferencias o giros detectados automáticamente',
                )
        else:
            if auto_categorized:
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

        # 9. Generar embedding
        embedding_text = f"{merchant} {subject}"
        embedding = None
        try:
            embedding = embeddings.embed_query(embedding_text)
        except Exception as e:
            logger.error(f"Error generando embedding para transacción Gmail: {e}")

        # 10. Crear el Expense o Income según el tipo detectado
        expense_obj = None
        income_obj = None
        if is_income:
            income_obj = Income.objects.create(
                user=user,
                amount=Decimal(str(amount)),
                currency=currency,
                description=merchant,
                timestamp=timezone.now(),
                received_at=txn_date,
                note=f"Detectado automáticamente desde Gmail: {subject[:200]}",
                raw_message=f"[Gmail] {subject}",
                user_income_category=category,
                embedding=embedding,
            )
            try:
                monthly_usage = user.get_current_monthly_usage()
                monthly_usage.increment_incomes()
            except Exception as e:
                logger.error(f"Error incrementando uso mensual de ingresos: {e}")
        else:
            expense_obj = Expense.objects.create(
                user=user,
                amount=Decimal(str(amount)),
                currency=currency,
                description=merchant,
                timestamp=timezone.now(),
                spent_at=txn_date,
                note=f"Detectado automáticamente desde Gmail: {subject[:200]}",
                raw_message=f"[Gmail] {subject}",
                user_expense_category=category,
                embedding=embedding,
            )
            try:
                monthly_usage = user.get_current_monthly_usage()
                monthly_usage.increment_expenses()
            except Exception as e:
                logger.error(f"Error incrementando uso mensual de gastos: {e}")

        # 11. Actualizar el email procesado
        # Dejamos awaiting_categorization=True incluso cuando se auto-categorizó,
        # para permitir corrección por texto plano. El intent-classifier filtra
        # falsos positivos.
        processed_email.is_purchase = True
        processed_email.transaction_type = transaction_type
        processed_email.expense = expense_obj
        processed_email.income = income_obj
        processed_email.processing_status = 'processed'
        processed_email.awaiting_categorization = True
        processed_email.save(update_fields=[
            'is_purchase', 'transaction_type', 'expense', 'income',
            'processing_status', 'awaiting_categorization',
            'ai_response', 'updated_at'
        ])

        logger.info(
            f"{transaction_type.capitalize()} creado desde Gmail para usuario "
            f"{user.id}: {amount} {currency} - {merchant}"
        )

        # 12. Enviar notificación por WhatsApp y guardar wamid para resolver
        # respuestas (swipe to reply o texto plano).
        try:
            sent_message_id = send_transaction_confirmation_whatsapp(
                user,
                income_obj or expense_obj,
                merchant,
                amount,
                currency,
                transaction_type=transaction_type,
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


def send_transaction_confirmation_whatsapp(
    user,
    transaction,
    merchant,
    amount,
    currency,
    transaction_type: str = 'expense',
    auto_categorized: bool = False,
    category_name: str = 'Sin Categorizar',
) -> str | None:
    """
    Envía un mensaje de WhatsApp al usuario informando sobre un gasto o ingreso
    detectado en Gmail. Si auto_categorized=True, solo informa; si no, pide
    que clasifique.

    Args:
        transaction: instancia de Expense o Income (se usa para obtener la
            fecha mostrada, spent_at / received_at).
        transaction_type: "expense" | "income".

    Returns:
        El wamid del mensaje enviado (para tracking de respuestas) o None si falla.
    """
    try:
        phone_number = user.phone_number
        if not phone_number:
            logger.info(f"Usuario {user.id} no tiene número de teléfono registrado")
            return None

        from whatsappbot.views import send_meta_whatsapp_message

        is_income = transaction_type == 'income'
        header = (
            "💵 *Ingreso detectado desde tu Gmail:*" if is_income
            else "📧 *Compra detectada desde tu Gmail:*"
        )
        source_label = "Fuente" if is_income else "Comercio"
        source_icon = "🏦" if is_income else "🏪"
        txn_noun = "ingreso" if is_income else "gasto"
        date_value = (
            getattr(transaction, 'received_at', None) if is_income
            else getattr(transaction, 'spent_at', None)
        )
        date_line = f"📅 *Fecha:* {date_value}\n" if date_value else ""

        if auto_categorized:
            message = (
                f"{header}\n\n"
                f"{source_icon} *{source_label}:* {merchant}\n"
                f"💰 *Monto:* {amount} {currency}\n"
                f"{date_line}"
                f"📁 *Categoría:* {category_name}\n\n"
                f"Registrado automáticamente. Si la categoría no es correcta, "
                f"respóndeme con la categoría correcta o desliza este mensaje "
                f"para responderlo directamente."
            )
        else:
            message = (
                f"{header}\n\n"
                f"{source_icon} *{source_label}:* {merchant}\n"
                f"💰 *Monto:* {amount} {currency}\n"
                f"{date_line}\n"
                f"He registrado este {txn_noun} en la categoría *Sin Categorizar*.\n"
                f"Responde con el nombre de la categoría para clasificarlo."
            )

        success, sent_message_id = send_meta_whatsapp_message(
            phone_number, message, return_message_id=True
        )
        if success:
            logger.info(
                f"Notificación de {txn_noun} enviada a {phone_number} "
                f"(wamid={sent_message_id})"
            )
            return sent_message_id
        else:
            logger.error(
                f"Error enviando notificación de {txn_noun} a {phone_number}"
            )
            return None

    except ImportError:
        logger.warning("whatsappbot no disponible para enviar notificaciones")
        return None
    except Exception as e:
        logger.error(f"Error enviando confirmación de transacción por WhatsApp: {e}")
        return None


# Alias retrocompatible por si algún otro módulo aún importa el nombre viejo.
send_purchase_confirmation_whatsapp = send_transaction_confirmation_whatsapp
