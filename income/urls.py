from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import IncomeViewSet, IncomeCategoryViewSet

router = DefaultRouter()
router.register('incomes', IncomeViewSet, basename='income')
router.register('categories', IncomeCategoryViewSet,
                basename='income-category')

urlpatterns = [
    path('', include(router.urls)),
]
