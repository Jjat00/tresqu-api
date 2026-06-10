"""Pipeline Composio -> Gmail.

Recibe un ``ProcessedEmail`` que el handler creo con el payload crudo
embebido en ``ai_response`` (bajo la clave ``_raw_email``) y ejecuta la
pipeline: AI parser, decision de categorizacion, creacion de
``Expense`` / ``Income``, embedding y notificacion por WhatsApp.

Reutiliza ``parse_purchase_email`` y
``send_transaction_confirmation_whatsapp`` del modulo legacy
``email_processor`` sin tocarlos: la unica diferencia es que esta
pipeline no fetcha de la Gmail API porque Composio nos entrega el email
completo en el webhook.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from categories.utils import (
    get_or_create_user_expense_category,
    get_or_create_user_income_category,
    get_user_categories_with_details,
)
from expenses.models import Expense
from gmailbot.email_processor import (
    AUTO_CATEGORIZATION_CONFIDENCE_THRESHOLD,
    parse_purchase_email,
    send_transaction_confirmation_whatsapp,
)
from income.models import Income

from .models import ProcessedEmail

logger = logging.getLogger(__name__)

# Distancia coseno maxima (text-embedding-3-small) para considerar que una
# transaccion pasada es "el mismo comercio recurrente" y heredar su categoria.
# Mas estricto que el umbral de busqueda semantica general: aqui una falsa
# coincidencia auto-asigna una categoria equivocada.
SEMANTIC_CATEGORY_MAX_DISTANCE = 0.40


def _semantic_category_fallback(user, embedding, is_income: bool) -> str | None:
    """
    Devuelve el nombre de la categoria mas frecuente entre las transacciones
    pasadas del usuario muy similares al embedding (mismo comercio), o None
    si no hay vecinos suficientemente cercanos ya categorizados.
    """
    try:
        model = Income if is_income else Expense
        category_attr = (
            "user_income_category" if is_income else "user_expense_category"
        )
        similar = model.find_similar(user, embedding, limit=5)
        votes: dict[str, int] = {}
        for txn in similar:
            if txn.distance > SEMANTIC_CATEGORY_MAX_DISTANCE:
                continue
            category = getattr(txn, category_attr)
            if not category or category.name == "Sin Categorizar":
                continue
            votes[category.name] = votes.get(category.name, 0) + 1
        if not votes:
            return None
        return max(votes, key=votes.get)
    except Exception as exc:
        logger.error(
            f"Error en fallback semantico de categoria para usuario "
            f"{user.id}: {exc}"
        )
        return None


def process_composio_email(pe: ProcessedEmail) -> None:
    """Pipeline AI + creacion de transaccion + WhatsApp para un ``ProcessedEmail``.

    El ``ProcessedEmail`` llega con ``_raw_email`` (subject/sender/body)
    embebido en ``ai_response`` — el handler lo guardo asi para no
    re-leer el webhook.
    """
    user = pe.user

    # 1. Recuperar raw_email serializado por el handler.
    try:
        stored: Any = json.loads(pe.ai_response) if pe.ai_response else {}
    except json.JSONDecodeError:
        stored = {}
    raw = stored.get("_raw_email") if isinstance(stored, dict) else None
    if not raw:
        logger.error(
            "composio pipeline missing _raw_email",
            extra={"processed_email_id": pe.id},
        )
        pe.processing_status = "error"
        pe.save(update_fields=["processing_status", "updated_at"])
        return

    subject: str = raw.get("subject", "") or ""
    sender: str = raw.get("sender", "") or ""
    body: str = raw.get("body", "") or ""

    # 2. Categorias del usuario para sugerencia del LLM.
    user_expense_categories: list[dict] = []
    user_income_categories: list[dict] = []
    try:
        cd = get_user_categories_with_details(user)
        user_expense_categories = cd.get("expense_categories", [])
        user_income_categories = cd.get("income_categories", [])
    except Exception as exc:
        logger.error(
            f"Error obteniendo categorias para sugerencia: {exc}",
            extra={"processed_email_id": pe.id, "user_id": user.id},
        )

    # 3. AI parser (reusa funcion legacy sin modificar).
    ai_result = parse_purchase_email(
        body,
        subject,
        sender,
        user_expense_categories=user_expense_categories,
        user_income_categories=user_income_categories,
    )
    if ai_result is None:
        pe.processing_status = "error"
        pe.ai_response = "AI parse returned None"
        pe.save(
            update_fields=["processing_status", "ai_response", "updated_at"]
        )
        return

    # 4. Determinar validez de la transaccion.
    payment_status = (ai_result.get("payment_status") or "unknown").lower()
    transaction_type = (ai_result.get("transaction_type") or "").lower()
    is_purchase = bool(ai_result.get("is_purchase", False))
    confidence = float(ai_result.get("confidence") or 0)
    is_valid_txn = (
        transaction_type in ("expense", "income") and is_purchase
    )

    if payment_status == "failed" or not is_valid_txn or confidence < 0.6:
        pe.processing_status = "skipped"
        pe.is_purchase = False
        pe.transaction_type = transaction_type or ""
        pe.ai_response = json.dumps(ai_result, ensure_ascii=False)
        pe.save(
            update_fields=[
                "processing_status",
                "is_purchase",
                "transaction_type",
                "ai_response",
                "updated_at",
            ]
        )
        logger.info(
            "composio pipeline skipped",
            extra={
                "processed_email_id": pe.id,
                "transaction_type": transaction_type,
                "is_purchase": is_purchase,
                "confidence": confidence,
                "payment_status": payment_status,
            },
        )
        return

    is_income = transaction_type == "income"

    # 5. Limites de plan del usuario.
    if is_income:
        can_add, limit_message = user.can_add_income()
    else:
        can_add, limit_message = user.can_add_expense()
    if not can_add:
        pe.processing_status = "skipped"
        pe.is_purchase = True
        pe.transaction_type = transaction_type
        pe.ai_response = json.dumps(
            {**ai_result, "skipped_reason": limit_message},
            ensure_ascii=False,
        )
        pe.save(
            update_fields=[
                "processing_status",
                "is_purchase",
                "transaction_type",
                "ai_response",
                "updated_at",
            ]
        )
        logger.warning(
            f"Limite de {transaction_type}s alcanzado para usuario {user.id}: "
            f"{limit_message}",
            extra={"processed_email_id": pe.id},
        )
        return

    # 6. Resolver datos comunes.
    merchant = ai_result.get("merchant") or (
        "Ingreso por email" if is_income else "Compra por email"
    )
    amount = ai_result.get("amount", 0)
    currency = ai_result.get("currency", user.default_currency)
    date_str = ai_result.get("date")
    if date_str:
        try:
            txn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            txn_date = timezone.now().date()
    else:
        txn_date = timezone.now().date()

    # 7. Embedding de la transaccion (se necesita aqui tanto para la busqueda
    # semantica posterior como para el fallback de categoria del paso 8).
    embedding = None
    try:
        from gmailbot.email_processor import embeddings as _embeddings

        embedding = _embeddings.embed_query(f"{merchant} {subject}")
    except Exception as exc:
        logger.error(
            f"Error generando embedding para transaccion Gmail: {exc}",
            extra={"processed_email_id": pe.id, "user_id": user.id},
        )

    # 8. Categorizacion automatica si el LLM esta seguro Y la categoria
    # existe en el set del usuario. Si el LLM no esta seguro, segundo
    # intento: la categoria que el usuario ya uso en transacciones
    # semanticamente similares (mismo comercio recurrente). Si tampoco,
    # fallback a "Sin Categorizar".
    suggested_category_name = ai_result.get("suggested_category")
    category_confidence = float(ai_result.get("category_confidence") or 0.0)
    relevant_categories = (
        user_income_categories if is_income else user_expense_categories
    )
    existing_lower = {c["name"].lower() for c in relevant_categories}
    auto_categorized = (
        bool(suggested_category_name)
        and suggested_category_name.lower() in existing_lower
        and category_confidence >= AUTO_CATEGORIZATION_CONFIDENCE_THRESHOLD
    )

    if not auto_categorized and embedding is not None:
        semantic_category = _semantic_category_fallback(user, embedding, is_income)
        if semantic_category:
            suggested_category_name = semantic_category
            auto_categorized = True
            ai_result["semantic_category_fallback"] = semantic_category
            logger.info(
                f"Categoria asignada por similitud semantica: "
                f"'{semantic_category}' para '{merchant}'",
                extra={"processed_email_id": pe.id, "user_id": user.id},
            )

    if is_income:
        if auto_categorized:
            category, _ = get_or_create_user_income_category(
                user, suggested_category_name
            )
        else:
            category, _ = get_or_create_user_income_category(
                user,
                "Sin Categorizar",
                description=(
                    "Ingresos detectados por Gmail pendientes de categorizacion"
                ),
                example=(
                    "Depositos, transferencias o giros detectados automaticamente"
                ),
            )
    else:
        if auto_categorized:
            category, _ = get_or_create_user_expense_category(
                user, suggested_category_name
            )
        else:
            category, _ = get_or_create_user_expense_category(
                user,
                "Sin Categorizar",
                description=(
                    "Gastos detectados por Gmail pendientes de categorizacion"
                ),
                examples=(
                    "Compras por email, pagos detectados automaticamente"
                ),
            )

    # 9. Crear Expense o Income segun el tipo detectado.
    expense_obj: Expense | None = None
    income_obj: Income | None = None
    if is_income:
        income_obj = Income.objects.create(
            user=user,
            amount=Decimal(str(amount)),
            currency=currency,
            description=merchant,
            timestamp=timezone.now(),
            received_at=txn_date,
            note=f"Detectado automaticamente desde Gmail: {subject[:200]}",
            raw_message=f"[Gmail] {subject}",
            user_income_category=category,
            embedding=embedding,
        )
        try:
            user.get_current_monthly_usage().increment_incomes()
        except Exception as exc:
            logger.error(
                f"Error incrementando uso mensual de ingresos: {exc}",
                extra={"processed_email_id": pe.id, "user_id": user.id},
            )
        pe.income = income_obj
    else:
        expense_obj = Expense.objects.create(
            user=user,
            amount=Decimal(str(amount)),
            currency=currency,
            description=merchant,
            timestamp=timezone.now(),
            spent_at=txn_date,
            note=f"Detectado automaticamente desde Gmail: {subject[:200]}",
            raw_message=f"[Gmail] {subject}",
            user_expense_category=category,
            embedding=embedding,
        )
        try:
            user.get_current_monthly_usage().increment_expenses()
        except Exception as exc:
            logger.error(
                f"Error incrementando uso mensual de gastos: {exc}",
                extra={"processed_email_id": pe.id, "user_id": user.id},
            )
        pe.expense = expense_obj

    # 10. Marcar el ProcessedEmail como procesado y persistir el ai_response
    # final (sin el envoltorio _raw_email; ya no se necesita).
    pe.processing_status = "processed"
    pe.is_purchase = True
    pe.transaction_type = transaction_type
    # Mantenemos awaiting_categorization=True siempre (incluso si auto)
    # porque el intent-classifier de WhatsApp permite correccion por texto
    # plano; esto es consistente con el legacy.
    pe.awaiting_categorization = True
    pe.ai_response = json.dumps(ai_result, ensure_ascii=False)
    pe.save(
        update_fields=[
            "processing_status",
            "is_purchase",
            "transaction_type",
            "awaiting_categorization",
            "ai_response",
            "expense",
            "income",
            "updated_at",
        ]
    )

    logger.info(
        f"{transaction_type.capitalize()} creado desde Gmail (Composio) para "
        f"usuario {user.id}: {amount} {currency} - {merchant}",
        extra={
            "processed_email_id": pe.id,
            "user_id": user.id,
            "transaction_type": transaction_type,
            "auto_categorized": auto_categorized,
        },
    )

    # 11. Notificacion por WhatsApp (reusa funcion legacy; firma:
    # user, transaction, merchant, amount, currency,
    # transaction_type, auto_categorized, category_name).
    try:
        sent_message_id = send_transaction_confirmation_whatsapp(
            user,
            income_obj or expense_obj,
            merchant,
            amount,
            currency,
            transaction_type=transaction_type,
            auto_categorized=auto_categorized,
            category_name=category.name if category else "Sin Categorizar",
        )
        if sent_message_id:
            pe.notification_message_id = sent_message_id
            pe.save(
                update_fields=["notification_message_id", "updated_at"]
            )
    except Exception as exc:
        logger.error(
            f"Error enviando notificacion WhatsApp: {exc}",
            extra={"processed_email_id": pe.id, "user_id": user.id},
        )
