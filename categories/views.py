from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import Category, UserExpenseCategory, UserIncomeCategory
from users.models import User
from .serializers import (
    CategorySerializer,
    UserExpenseCategorySerializer,
    UserIncomeCategorySerializer,
    UserCategoriesResponseSerializer
)
from .utils import (
    get_user_expense_categories,
    get_user_income_categories,
    get_user_categories_with_details,
    get_user_expense_categories_queryset,
    get_user_income_categories_queryset
)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet para categorías globales (legacy - solo lectura)"""
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='all')
    def get_all_categories(self, request):
        """Endpoint legacy para obtener todas las categorías globales"""
        categories = Category.objects.all()
        serializer = self.get_serializer(categories, many=True)
        return Response({
            'categories': serializer.data,
            'total': categories.count()
        })


class UserExpenseCategoryViewSet(viewsets.ModelViewSet):
    """ViewSet para categorías de gastos por usuario"""
    serializer_class = UserExpenseCategorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filtrar categorías por usuario autenticado"""
        return UserExpenseCategory.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        """Asignar usuario automáticamente al crear"""
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'], url_path='predefined')
    def get_predefined(self, request):
        """Obtener solo categorías predefinidas del usuario"""
        predefined = self.get_queryset().filter(is_default=True)
        serializer = self.get_serializer(predefined, many=True)
        return Response({
            'categories': serializer.data,
            'total': predefined.count()
        })

    @action(detail=False, methods=['get'], url_path='custom')
    def get_custom(self, request):
        """Obtener solo categorías personalizadas del usuario"""
        custom = self.get_queryset().filter(is_default=False)
        serializer = self.get_serializer(custom, many=True)
        return Response({
            'categories': serializer.data,
            'total': custom.count()
        })

    @action(detail=False, methods=['post'], url_path='get-or-create')
    def get_or_create_category(self, request):
        """Obtener o crear una categoría por nombre"""
        name = request.data.get('name')
        if not name:
            return Response(
                {'error': 'El nombre de la categoría es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        from .utils import get_or_create_user_expense_category
        category = get_or_create_user_expense_category(request.user, name)
        serializer = self.get_serializer(category)
        return Response(serializer.data)


class UserIncomeCategoryViewSet(viewsets.ModelViewSet):
    """ViewSet para categorías de ingresos por usuario"""
    serializer_class = UserIncomeCategorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filtrar categorías por usuario autenticado"""
        return UserIncomeCategory.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        """Asignar usuario automáticamente al crear"""
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'], url_path='predefined')
    def get_predefined(self, request):
        """Obtener solo categorías predefinidas del usuario"""
        predefined = self.get_queryset().filter(is_default=True)
        serializer = self.get_serializer(predefined, many=True)
        return Response({
            'categories': serializer.data,
            'total': predefined.count()
        })

    @action(detail=False, methods=['get'], url_path='custom')
    def get_custom(self, request):
        """Obtener solo categorías personalizadas del usuario"""
        custom = self.get_queryset().filter(is_default=False)
        serializer = self.get_serializer(custom, many=True)
        return Response({
            'categories': serializer.data,
            'total': custom.count()
        })

    @action(detail=False, methods=['post'], url_path='get-or-create')
    def get_or_create_category(self, request):
        """Obtener o crear una categoría por nombre"""
        name = request.data.get('name')
        if not name:
            return Response(
                {'error': 'El nombre de la categoría es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        from .utils import get_or_create_user_income_category
        category = get_or_create_user_income_category(request.user, name)
        serializer = self.get_serializer(category)
        return Response(serializer.data)


class UserCategoriesViewSet(viewsets.ViewSet):
    """ViewSet para obtener todas las categorías del usuario de forma combinada"""
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='all')
    def get_all_user_categories(self, request):
        """Obtener todas las categorías del usuario (gastos e ingresos)"""
        user = request.user

        # Obtener categorías usando las funciones que devuelven QuerySets
        expense_categories = get_user_expense_categories_queryset(user)
        income_categories = get_user_income_categories_queryset(user)

        response_data = {
            'expense_categories': UserExpenseCategorySerializer(
                expense_categories, many=True
            ).data,
            'income_categories': UserIncomeCategorySerializer(
                income_categories, many=True
            ).data,
            'totals': {
                'expense_categories': expense_categories.count(),
                'income_categories': income_categories.count()
            }
        }

        return Response(response_data)

    @action(detail=False, methods=['get'], url_path='with-details')
    def get_categories_with_details(self, request):
        """Obtener categorías con descripciones, ejemplos y colores"""
        user = request.user
        categories_data = get_user_categories_with_details(user)

        return Response({
            'expense_categories': categories_data['expense_categories'],
            'income_categories': categories_data['income_categories'],
            'totals': {
                'expense_categories': len(categories_data['expense_categories']),
                'income_categories': len(categories_data['income_categories'])
            }
        })

    @action(detail=False, methods=['get'], url_path='summary')
    def get_categories_summary(self, request):
        """Obtener resumen de categorías del usuario"""
        user = request.user

        expense_categories = get_user_expense_categories_queryset(user)
        income_categories = get_user_income_categories_queryset(user)

        expense_predefined = expense_categories.filter(is_default=True).count()
        expense_custom = expense_categories.filter(is_default=False).count()

        income_predefined = income_categories.filter(is_default=True).count()
        income_custom = income_categories.filter(is_default=False).count()

        return Response({
            'summary': {
                'expense_categories': {
                    'total': expense_categories.count(),
                    'predefined': expense_predefined,
                    'custom': expense_custom
                },
                'income_categories': {
                    'total': income_categories.count(),
                    'predefined': income_predefined,
                    'custom': income_custom
                }
            }
        })

    @action(detail=False, methods=['post'], url_path='search')
    def search_categories(self, request):
        """Buscar categorías por nombre"""
        query = request.data.get('query', '').strip()
        category_type = request.data.get(
            'type', 'all')  # 'expense', 'income', 'all'

        if not query:
            return Response(
                {'error': 'Query de búsqueda es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user
        results = {'expense_categories': [], 'income_categories': []}

        if category_type in ['expense', 'all']:
            expense_categories = UserExpenseCategory.objects.filter(
                user=user,
                name__icontains=query
            )
            results['expense_categories'] = UserExpenseCategorySerializer(
                expense_categories, many=True
            ).data

        if category_type in ['income', 'all']:
            income_categories = UserIncomeCategory.objects.filter(
                user=user,
                name__icontains=query
            )
            results['income_categories'] = UserIncomeCategorySerializer(
                income_categories, many=True
            ).data

        return Response(results)
