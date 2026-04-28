"""
Mercado Pago integration for "suscripción con plan asociado".

Flow:
    1. Operator ensures a MercadoPagoPlan row exists for each
       (SubscriptionPlan, is_yearly) pair — created lazily on first checkout.
    2. Frontend tokenizes the user's card with the MP JS SDK and posts
       card_token_id to /api/subscriptions/checkout/.
    3. Backend creates an MP preapproval in status="authorized" and records
       the resulting mp_preapproval_id on our Subscription.
    4. MP webhook notifications update the subscription status.

Docs: https://www.mercadopago.com.co/developers/en/docs/subscriptions/integration-configuration/subscription-plan
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal
from typing import Any, Optional

import requests
from django.conf import settings
from django.db import transaction

from .models import MercadoPagoPlan, SubscriptionPlan

logger = logging.getLogger(__name__)

MP_BASE_URL = "https://api.mercadopago.com"


class MercadoPagoError(Exception):
    """Raised when Mercado Pago returns an error response."""

    def __init__(self, message: str, status: Optional[int] = None,
                 payload: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _auth_headers() -> dict:
    token = settings.MERCADOPAGO_ACCESS_TOKEN
    if not token:
        raise MercadoPagoError(
            "MERCADOPAGO_ACCESS_TOKEN is not configured")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, *, json: Any = None,
             context: str = "") -> dict:
    url = f"{MP_BASE_URL}{path}"
    try:
        resp = requests.request(
            method, url, headers=_auth_headers(), json=json, timeout=15)
    except requests.RequestException as exc:
        logger.exception("Mercado Pago %s network error", context)
        raise MercadoPagoError(
            f"Network error calling Mercado Pago: {exc}") from exc

    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}

    if resp.status_code >= 400:
        message = (body.get("message") if isinstance(body, dict) else None) \
            or context or resp.text
        logger.error(
            "Mercado Pago %s failed (status=%s): %s",
            context, resp.status_code, body)
        raise MercadoPagoError(
            message, status=resp.status_code, payload=body)
    return body


def _plan_amount(plan: SubscriptionPlan, is_yearly: bool) -> Decimal:
    """
    Returns the charge amount for (plan, cycle) in MERCADOPAGO_CURRENCY.
    Prices are stored as-is on SubscriptionPlan (COP, no conversion).
    """
    return Decimal(plan.price_yearly if is_yearly else plan.price_monthly)


def get_or_create_preapproval_plan(
    plan: SubscriptionPlan, is_yearly: bool
) -> MercadoPagoPlan:
    """
    Returns the MercadoPagoPlan for (plan, is_yearly), creating it in MP
    on first use. BASIC plans must not be passed here.
    """
    if plan.name == 'BASIC':
        raise MercadoPagoError("BASIC plan has no MP preapproval_plan")

    currency = settings.MERCADOPAGO_CURRENCY
    existing = MercadoPagoPlan.objects.filter(
        subscription_plan=plan,
        is_yearly=is_yearly,
        currency_id=currency,
    ).first()
    if existing:
        return existing

    amount = _plan_amount(plan, is_yearly)
    frequency_type = 'months'
    frequency = 12 if is_yearly else 1
    reason = f"Tresqu {plan.get_name_display()} " \
        f"({'anual' if is_yearly else 'mensual'})"

    payload = {
        "reason": reason,
        "auto_recurring": {
            "frequency": frequency,
            "frequency_type": frequency_type,
            "transaction_amount": float(amount),
            "currency_id": currency,
        },
        "payment_methods_allowed": {
            "payment_types": [{}],
            "payment_methods": [{}],
        },
        "back_url": settings.MERCADOPAGO_BACK_URL,
    }

    body = _request(
        "POST", "/preapproval_plan",
        json=payload, context="preapproval_plan.create")
    preapproval_plan_id = body.get("id")
    if not preapproval_plan_id:
        raise MercadoPagoError(
            "preapproval_plan response missing id", payload=body)

    with transaction.atomic():
        mp_plan, _ = MercadoPagoPlan.objects.get_or_create(
            subscription_plan=plan,
            is_yearly=is_yearly,
            currency_id=currency,
            defaults={
                'preapproval_plan_id': preapproval_plan_id,
                'transaction_amount': amount,
            },
        )
    return mp_plan


def create_subscription(
    *, mp_plan: MercadoPagoPlan, payer_email: str,
    card_token_id: str, external_reference: str,
) -> dict:
    """
    Creates a Mercado Pago preapproval (subscription) in status='authorized'.
    Returns the MP response body — caller persists the id/status on our
    Subscription row.
    """
    payload = {
        "preapproval_plan_id": mp_plan.preapproval_plan_id,
        "reason": f"Tresqu {mp_plan.subscription_plan.get_name_display()}",
        "external_reference": external_reference,
        "payer_email": payer_email,
        "card_token_id": card_token_id,
        "auto_recurring": {
            "frequency": 12 if mp_plan.is_yearly else 1,
            "frequency_type": "months",
            "transaction_amount": float(mp_plan.transaction_amount),
            "currency_id": mp_plan.currency_id,
        },
        "back_url": settings.MERCADOPAGO_BACK_URL,
        "status": "authorized",
    }
    return _request(
        "POST", "/preapproval", json=payload, context="preapproval.create")


def fetch_preapproval(preapproval_id: str) -> dict:
    return _request(
        "GET", f"/preapproval/{preapproval_id}",
        context="preapproval.get")


def cancel_preapproval(preapproval_id: str) -> dict:
    return _request(
        "PUT", f"/preapproval/{preapproval_id}",
        json={"status": "cancelled"}, context="preapproval.cancel")


def verify_webhook_signature(
    *, signature_header: str, request_id: str, data_id: str,
) -> bool:
    """
    Validates Mercado Pago's `x-signature` header.
    Format: "ts=<timestamp>,v1=<hmac-sha256>"
    Template: id:<data_id>;request-id:<request_id>;ts:<timestamp>;
    """
    secret = settings.MERCADOPAGO_WEBHOOK_SECRET
    if not secret:
        logger.warning(
            "MERCADOPAGO_WEBHOOK_SECRET not set — skipping signature check")
        return True
    if not signature_header:
        return False

    parts = dict(
        p.strip().split('=', 1) for p in signature_header.split(',')
        if '=' in p
    )
    ts = parts.get('ts')
    v1 = parts.get('v1')
    if not ts or not v1:
        return False

    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    expected = hmac.new(
        secret.encode(), manifest.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, v1)
