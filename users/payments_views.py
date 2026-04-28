"""
Mercado Pago checkout + webhook views.

Endpoints:
    POST /api/subscriptions/checkout/        — create MP preapproval for current user
    POST /api/subscriptions/cancel_current/  — cancel active MP preapproval
    GET  /api/subscriptions/current/         — return current active subscription
    POST /api/webhooks/mercadopago/          — receive MP notifications
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from . import payment_gateway as mp
from .models import MercadoPagoPlan, Subscription, SubscriptionPlan, User
from .serializers import SubscriptionSerializer

logger = logging.getLogger(__name__)


def _compute_end_date(start, is_yearly: bool):
    return start + (timedelta(days=365) if is_yearly else timedelta(days=30))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def checkout(request):
    """
    Body: { plan_id: int, is_yearly: bool, card_token_id: str, payer_email: str }
    """
    user: User = request.user
    plan_id = request.data.get('plan_id')
    is_yearly = bool(request.data.get('is_yearly', False))
    card_token_id = request.data.get('card_token_id')
    payer_email = request.data.get('payer_email')

    if not plan_id or not card_token_id or not payer_email:
        return Response(
            {"error": "plan_id, card_token_id and payer_email are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        plan = SubscriptionPlan.objects.get(pk=plan_id, is_active=True)
    except SubscriptionPlan.DoesNotExist:
        return Response({"error": "Plan not found"},
                        status=status.HTTP_404_NOT_FOUND)

    if plan.name == 'BASIC':
        return Response({"error": "BASIC plan is free and cannot be purchased"},
                        status=status.HTTP_400_BAD_REQUEST)

    active = Subscription.objects.filter(
        user=user, is_active=True, mp_status='authorized'
    ).first()
    if active:
        return Response(
            {"error": "Ya tienes una suscripción activa. Cancélala antes "
             "de cambiar de plan.",
             "subscription": SubscriptionSerializer(active).data},
            status=status.HTTP_409_CONFLICT,
        )

    try:
        mp_plan = mp.get_or_create_preapproval_plan(plan, is_yearly)
    except mp.MercadoPagoError as exc:
        logger.exception("Failed to get/create preapproval_plan")
        return Response({"error": str(exc), "mp_payload": exc.payload},
                        status=status.HTTP_502_BAD_GATEWAY)

    external_reference = f"user-{user.id}-plan-{plan.id}-" \
        f"{'y' if is_yearly else 'm'}-{int(timezone.now().timestamp())}"

    try:
        mp_resp = mp.create_subscription(
            mp_plan=mp_plan,
            payer_email=payer_email,
            card_token_id=card_token_id,
            external_reference=external_reference,
        )
    except mp.MercadoPagoError as exc:
        logger.exception("Failed to create preapproval")
        return Response({"error": str(exc), "mp_payload": exc.payload},
                        status=status.HTTP_402_PAYMENT_REQUIRED)

    mp_preapproval_id = mp_resp.get('id')
    mp_status = mp_resp.get('status', 'pending')

    start_date = timezone.now()
    end_date = _compute_end_date(start_date, is_yearly)

    with transaction.atomic():
        Subscription.objects.filter(user=user, is_active=True).update(
            is_active=False, end_date=start_date)

        sub = Subscription.objects.create(
            user=user,
            plan=plan,
            start_date=start_date,
            end_date=end_date,
            is_active=True,
            amount_paid=mp_plan.transaction_amount,
            is_yearly_billing=is_yearly,
            payment_reference=mp_preapproval_id,
            mp_preapproval_id=mp_preapproval_id,
            mp_status=mp_status,
        )

        user.subscription_plan = plan
        user.subscription_active = True
        user.subscription_start_date = start_date
        user.subscription_end_date = end_date
        user.is_yearly_billing = is_yearly
        user.save(update_fields=[
            'subscription_plan', 'subscription_active',
            'subscription_start_date', 'subscription_end_date',
            'is_yearly_billing', 'updated_at',
        ])

    return Response(
        {"subscription": SubscriptionSerializer(sub).data,
         "mp_status": mp_status},
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_current(request):
    user: User = request.user
    sub = Subscription.objects.filter(
        user=user, is_active=True
    ).exclude(mp_preapproval_id__isnull=True).first()
    if not sub:
        return Response({"error": "No hay suscripción activa para cancelar"},
                        status=status.HTTP_404_NOT_FOUND)

    try:
        mp.cancel_preapproval(sub.mp_preapproval_id)
    except mp.MercadoPagoError as exc:
        logger.exception("Failed to cancel preapproval")
        return Response({"error": str(exc), "mp_payload": exc.payload},
                        status=status.HTTP_502_BAD_GATEWAY)

    now = timezone.now()
    sub.is_active = False
    sub.mp_status = 'cancelled'
    sub.end_date = now
    sub.save(update_fields=[
        'is_active', 'mp_status', 'end_date', 'updated_at'])

    user.downgrade_to_basic()
    return Response({"message": "Suscripción cancelada"},
                    status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def current(request):
    sub = Subscription.objects.filter(
        user=request.user, is_active=True
    ).order_by('-start_date').first()
    if not sub:
        return Response({"subscription": None})
    return Response({"subscription": SubscriptionSerializer(sub).data})


@api_view(['POST'])
@permission_classes([AllowAny])
def mercadopago_webhook(request):
    """
    Mercado Pago notifications webhook.

    MP sends topics such as `preapproval`, `subscription_preapproval`,
    `subscription_authorized_payment`, `payment`. We only act on the
    preapproval-related ones — payments are observable via fetch_preapproval.

    Query params: ?type=<topic>&data.id=<id>  (or body: { type, data: { id } })
    """
    payload = request.data if isinstance(request.data, dict) else {}
    topic = (request.query_params.get('type')
             or request.query_params.get('topic')
             or payload.get('type')
             or payload.get('topic'))
    data_id = (request.query_params.get('data.id')
               or (payload.get('data') or {}).get('id')
               or payload.get('id'))

    signature_ok = mp.verify_webhook_signature(
        signature_header=request.headers.get('x-signature', ''),
        request_id=request.headers.get('x-request-id', ''),
        data_id=str(data_id or ''),
    )
    if not signature_ok:
        logger.warning("Rejected MP webhook: invalid signature")
        return Response({"error": "invalid signature"},
                        status=status.HTTP_401_UNAUTHORIZED)

    logger.info("MP webhook received topic=%s data_id=%s", topic, data_id)

    if topic in ('preapproval', 'subscription_preapproval') and data_id:
        try:
            preapproval = mp.fetch_preapproval(str(data_id))
        except mp.MercadoPagoError:
            logger.exception("MP webhook: fetch_preapproval failed")
            return Response({"ok": False}, status=status.HTTP_200_OK)

        sub = Subscription.objects.filter(
            mp_preapproval_id=preapproval.get('id')).first()
        if not sub:
            logger.info("MP webhook: no local subscription for id=%s",
                        preapproval.get('id'))
            return Response({"ok": True}, status=status.HTTP_200_OK)

        new_status = preapproval.get('status')
        sub.mp_status = new_status
        next_payment = preapproval.get('next_payment_date')
        if next_payment:
            sub.next_payment_date = next_payment
        if new_status in ('cancelled', 'finished'):
            sub.is_active = False
            sub.end_date = timezone.now()
            user = sub.user
            if user.subscription_plan_id == sub.plan_id:
                user.downgrade_to_basic()
        sub.save()

    return Response({"ok": True}, status=status.HTTP_200_OK)
