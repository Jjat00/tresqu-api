import json
import logging

from django.conf import settings
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from expenses.models import Expense  # noqa: F401  re-exported for callers
from income.models import Income  # noqa: F401  re-exported for callers
from categories.utils import (  # noqa: F401  re-exported for callers
    get_or_create_user_expense_category,
    get_or_create_user_income_category,
    get_user_categories_with_details,
)
from telegrambot.tools import embeddings  # noqa: F401  re-exported for callers

from .models import ProcessedEmail  # noqa: F401  re-exported for callers

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
        suggested_category, category_confidence, transaction_reference
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
    "category_confidence": 0.0,
    "transaction_reference": "Identificador único de la transacción o null"
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
- date: fecha de la transacción en YYYY-MM-DD, o null si no aparece. OJO con el
  formato de origen: bancos y comercios de Latinoamérica y Europa escriben
  DD/MM/YYYY o DD/MM/YY ("22/08/26 a las 16:53" = 2026-08-22; "02/08/2026" = 2 de
  agosto de 2026). NUNCA lo leas como MM/DD ni como YY/MM/DD; un año de dos
  dígitos es 20YY. Si dudas entre dos lecturas, devuelve null (se usará la fecha
  de recepción del correo).
- confidence: 0.0 a 1.0 sobre si es transacción real Y exitosa.
- suggested_category: EXACTAMENTE uno de los nombres de la lista del tipo detectado
  (gasto si transaction_type="expense", ingreso si "income"). Si ninguna encaja,
  null. NO mezcles: no sugieras una categoría de gasto para un ingreso o viceversa.
- category_confidence: 0.0 a 1.0. Usa >=0.8 solo si el contexto hace la categoría
  obvia (ej. empleador → Salario, Uber → Transporte, Rappi → Alimentación).
- transaction_reference: identificador único de ESTA transacción si el email lo
  incluye: número de recibo/orden/factura ("#2749-9904", "Order 112-334"), ID de
  transacción o autorización. NO uses números de cuenta, últimos 4 dígitos de la
  tarjeta ni números de cliente (no identifican la transacción). Si no hay, null.

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


def _record_notification_as_message(user, phone_number, text, sent_message_id):
    """
    Guarda la notificación de Gmail como mensaje saliente en el chat de
    WhatsApp del usuario, igual que cualquier respuesta del bot: queda en el
    historial que ve el agente (fetch_last_messages) y se vuelve citable vía
    _get_quoted_message_text. create_message genera el embedding del texto.
    """
    try:
        from whatsappbot.bot import (
            create_message,
            get_or_create_chat,
            update_chat_user,
        )
        from whatsappbot.utils import normalize_phone_number

        chat, _ = get_or_create_chat(normalize_phone_number(phone_number))
        if chat.user_id is None:
            update_chat_user(chat, user)
        create_message(chat, sent_message_id or "", "outgoing", text)
    except Exception as e:
        logger.error(
            f"Error registrando notificación de Gmail como mensaje de chat "
            f"para usuario {user.id}: {e}"
        )


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
            _record_notification_as_message(
                user, phone_number, message, sent_message_id
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
