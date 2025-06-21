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
router.register(r'expenses', UserExpenseCategoryViewSet,
                basename='user-expense-category')
router.register(r'incomes', UserIncomeCategoryViewSet,
                basename='user-income-category')
router.register(r'', UserCategoriesViewSet, basename='user-categories')

urlpatterns = [
    path('', include(router.urls)),
]

# URLs disponibles:
# GET    /api/categories/legacy/                         - Listar categorías globales (legacy)
# GET    /api/categories/legacy/all/                     - Todas las categorías globales
#
# GET    /api/categories/expenses/                       - Listar categorías de gastos del usuario
# POST   /api/categories/expenses/                       - Crear nueva categoría de gasto
# GET    /api/categories/expenses/{id}/                  - Detalle de categoría de gasto
# PUT    /api/categories/expenses/{id}/                  - Actualizar categoría de gasto
# DELETE /api/categories/expenses/{id}/                  - Eliminar categoría de gasto
# GET    /api/categories/expenses/predefined/            - Solo categorías predefinidas
# GET    /api/categories/expenses/custom/                - Solo categorías personalizadas
# GET    /api/categories/expenses/colors-map/            - Mapa de colores para visualizaciones
# GET    /api/categories/expenses/with-usage/            - Categorías con estadísticas de uso
# GET    /api/categories/expenses/search/                - Buscar categorías por nombre
# GET    /api/categories/expenses/popular/               - Categorías más usadas
# GET    /api/categories/expenses/recent/                - Categorías usadas recientemente
# POST   /api/categories/expenses/bulk-create/           - Crear múltiples categorías
# PATCH  /api/categories/expenses/bulk-update/           - Actualizar múltiples categorías
# DELETE /api/categories/expenses/bulk-delete/           - Eliminar múltiples categorías
# GET    /api/categories/expenses/export/                - Exportar categorías del usuario
# POST   /api/categories/expenses/import/                - Importar categorías
# POST   /api/categories/expenses/reset-to-defaults/     - Restaurar categorías predefinidas
# POST   /api/categories/expenses/get-or-create/         - Obtener o crear categoría
#
# GET    /api/categories/incomes/                        - Listar categorías de ingresos del usuario
# POST   /api/categories/incomes/                        - Crear nueva categoría de ingreso
# GET    /api/categories/incomes/{id}/                   - Detalle de categoría de ingreso
# PUT    /api/categories/incomes/{id}/                   - Actualizar categoría de ingreso
# DELETE /api/categories/incomes/{id}/                   - Eliminar categoría de ingreso
# GET    /api/categories/incomes/predefined/             - Solo categorías predefinidas
# GET    /api/categories/incomes/custom/                 - Solo categorías personalizadas
# GET    /api/categories/incomes/colors-map/             - Mapa de colores para visualizaciones
# GET    /api/categories/incomes/with-usage/             - Categorías con estadísticas de uso
# GET    /api/categories/incomes/search/                 - Buscar categorías por nombre
# GET    /api/categories/incomes/popular/                - Categorías más usadas
# GET    /api/categories/incomes/recent/                 - Categorías usadas recientemente
# POST   /api/categories/incomes/bulk-create/            - Crear múltiples categorías
# PATCH  /api/categories/incomes/bulk-update/            - Actualizar múltiples categorías
# DELETE /api/categories/incomes/bulk-delete/            - Eliminar múltiples categorías
# GET    /api/categories/incomes/export/                 - Exportar categorías del usuario
# POST   /api/categories/incomes/import/                 - Importar categorías
# POST   /api/categories/incomes/reset-to-defaults/      - Restaurar categorías predefinidas
# POST   /api/categories/incomes/get-or-create/          - Obtener o crear categoría
#
# GET    /api/categories/all/                            - Todas las categorías del usuario
# GET    /api/categories/with-details/                   - Categorías con detalles completos
# GET    /api/categories/summary/                        - Resumen de categorías
# POST   /api/categories/search/                         - Buscar categorías
