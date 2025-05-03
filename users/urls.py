from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UserViewSet, SubscriptionPlanViewSet, SubscriptionViewSet, OrganizationViewSet, OrganizationInvitationViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'subscription-plans', SubscriptionPlanViewSet)
router.register(r'subscriptions', SubscriptionViewSet)
router.register(r'organizations', OrganizationViewSet)
router.register(r'organization-invitations', OrganizationInvitationViewSet)

urlpatterns = router.urls
