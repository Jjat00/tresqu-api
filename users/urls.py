from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UserViewSet,
    SubscriptionPlanViewSet,
    SubscriptionViewSet,
    OrganizationViewSet,
    OrganizationInvitationViewSet,
    TrackingLinkViewSet,
    telegram_auth_widget,
    request_verification_code,
    verify_code
)
from .payments_views import (
    checkout as mp_checkout,
    cancel_current as mp_cancel_current,
    current as mp_current,
    mercadopago_webhook,
)

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'subscription-plans', SubscriptionPlanViewSet)
router.register(r'subscriptions', SubscriptionViewSet)
router.register(r'organizations', OrganizationViewSet)
router.register(r'organization-invitations', OrganizationInvitationViewSet)
router.register(r'tracking-links', TrackingLinkViewSet)

urlpatterns = [
    # Mercado Pago subscriptions — declared BEFORE router so they don't
    # get shadowed by SubscriptionViewSet's detail route.
    path('subscriptions/checkout/', mp_checkout, name='mp_checkout'),
    path('subscriptions/cancel_current/', mp_cancel_current,
         name='mp_cancel_current'),
    path('subscriptions/current/', mp_current, name='mp_current'),
    path('webhooks/mercadopago/', mercadopago_webhook,
         name='mercadopago_webhook'),

    # URLs para autenticación Telegram
    path('auth/telegram/widget/', telegram_auth_widget,
         name='telegram_auth_widget'),
    path('auth/telegram/request-code/', request_verification_code,
         name='request_verification_code'),
    path('auth/telegram/verify-code/', verify_code, name='verify_code'),
] + router.urls
