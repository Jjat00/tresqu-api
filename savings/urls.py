from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SavingsCategoryViewSet,
    SavingsGoalViewSet,
    SavingsDepositViewSet,
    SavingsTemplateViewSet
)

# Crear router para las APIs
router = DefaultRouter()
router.register(r'categories', SavingsCategoryViewSet,
                basename='savings-categories')
router.register(r'goals', SavingsGoalViewSet, basename='savings-goals')
router.register(r'deposits', SavingsDepositViewSet,
                basename='savings-deposits')
router.register(r'templates', SavingsTemplateViewSet,
                basename='savings-templates')

urlpatterns = [
    path('', include(router.urls)),
]
