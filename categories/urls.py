from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoryViewSet,
    UserExpenseCategoryViewSet,
    UserIncomeCategoryViewSet,
    UserCategoriesViewSet
)

# Crear router para las ViewSets
router = DefaultRouter()
router.register(r'legacy', CategoryViewSet, basename='category')
router.register(r'expense', UserExpenseCategoryViewSet,
                basename='user-expense-category')
router.register(r'income', UserIncomeCategoryViewSet,
                basename='user-income-category')
router.register(r'user', UserCategoriesViewSet, basename='user-categories')

urlpatterns = [
    path('api/', include(router.urls)),
]

# URLs disponibles:
# GET    /api/categories/api/legacy/                     - Listar categorías globales (legacy)
# GET    /api/categories/api/legacy/all/                 - Todas las categorías globales
#
# GET    /api/categories/api/expense/                    - Listar categorías de gastos del usuario
# POST   /api/categories/api/expense/                    - Crear nueva categoría de gasto
# GET    /api/categories/api/expense/{id}/               - Detalle de categoría de gasto
# PUT    /api/categories/api/expense/{id}/               - Actualizar categoría de gasto
# DELETE /api/categories/api/expense/{id}/               - Eliminar categoría de gasto
# GET    /api/categories/api/expense/predefined/         - Solo categorías predefinidas
# GET    /api/categories/api/expense/custom/             - Solo categorías personalizadas
# POST   /api/categories/api/expense/get-or-create/      - Obtener o crear categoría
#
# GET    /api/categories/api/income/                     - Listar categorías de ingresos del usuario
# POST   /api/categories/api/income/                     - Crear nueva categoría de ingreso
# GET    /api/categories/api/income/{id}/                - Detalle de categoría de ingreso
# PUT    /api/categories/api/income/{id}/                - Actualizar categoría de ingreso
# DELETE /api/categories/api/income/{id}/                - Eliminar categoría de ingreso
# GET    /api/categories/api/income/predefined/          - Solo categorías predefinidas
# GET    /api/categories/api/income/custom/              - Solo categorías personalizadas
# POST   /api/categories/api/income/get-or-create/       - Obtener o crear categoría
#
# GET    /api/categories/api/user/all/                   - Todas las categorías del usuario
# GET    /api/categories/api/user/with-details/          - Categorías con detalles completos
# GET    /api/categories/api/user/summary/               - Resumen de categorías
# POST   /api/categories/api/user/search/                - Buscar categorías
