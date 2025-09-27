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

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'subscription-plans', SubscriptionPlanViewSet)
router.register(r'subscriptions', SubscriptionViewSet)
router.register(r'organizations', OrganizationViewSet)
router.register(r'organization-invitations', OrganizationInvitationViewSet)
router.register(r'tracking-links', TrackingLinkViewSet)

urlpatterns = router.urls + [
    # URLs para autenticación Telegram
    path('auth/telegram/widget/', telegram_auth_widget,
         name='telegram_auth_widget'),
    path('auth/telegram/request-code/', request_verification_code,
         name='request_verification_code'),
    path('auth/telegram/verify-code/', verify_code, name='verify_code'),
]
