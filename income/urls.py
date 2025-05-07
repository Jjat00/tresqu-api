from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import IncomeViewSet

router = DefaultRouter()
router.register('incomes', IncomeViewSet, basename='income')

urlpatterns = [
    path('', include(router.urls)),
]
