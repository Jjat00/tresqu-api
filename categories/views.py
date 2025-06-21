from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Count
from django.utils import timezone
from datetime import timedelta
import json
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

    @action(detail=False, methods=['get'], url_path='colors-map')
    def colors_map(self, request):
        """Obtener mapa de colores para visualizaciones"""
        categories = self.get_queryset()
        colors_map = {}

        for category in categories:
            colors_map[category.name] = category.color

        if not colors_map:
            # Fallback con colores default
            colors_map = {
                'Sin categoría': '#9E9E9E'
            }

        return Response(colors_map)

    @action(detail=False, methods=['get'], url_path='with-usage')
    def with_usage(self, request):
        """Categorías con estadísticas de uso"""
        days = int(request.query_params.get('days', 30))
        since_date = timezone.now() - timedelta(days=days)

        categories = self.get_queryset()
        categories_with_usage = []

        for category in categories:
            # Obtener estadísticas de uso
            from expenses.models import Expense
            expenses = Expense.objects.filter(
                user=request.user,
                user_expense_category=category,
                spent_at__gte=since_date
            )

            usage_count = expenses.count()
            total_amount = sum(expense.amount for expense in expenses)
            avg_amount = total_amount / usage_count if usage_count > 0 else 0
            last_expense = expenses.order_by('-spent_at').first()

            category_data = self.get_serializer(category).data
            category_data['usage_stats'] = {
                'usage_count': usage_count,
                'total_amount': float(total_amount),
                'avg_amount': float(avg_amount),
                'last_used': last_expense.spent_at if last_expense else None,
                'days_since_last_use': (timezone.now().date() - last_expense.spent_at).days if last_expense else None
            }
            categories_with_usage.append(category_data)

        return Response({
            'categories': categories_with_usage,
            'totals': {
                'categories': categories.count(),
                'predefined': categories.filter(is_default=True).count(),
                'custom': categories.filter(is_default=False).count(),
                'total_usage': sum(cat['usage_stats']['usage_count'] for cat in categories_with_usage)
            }
        })

    @action(detail=False, methods=['get'], url_path='search')
    def search(self, request):
        """Buscar categorías por nombre"""
        query = request.query_params.get('q', '').strip()

        if not query:
            return Response(
                {'error': 'Parámetro q es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        categories = self.get_queryset().filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(examples__icontains=query)
        )

        results = []
        for category in categories:
            result = self.get_serializer(category).data
            result['type'] = 'expense'
            result['match_score'] = 1.0 if query.lower(
            ) in category.name.lower() else 0.8
            results.append(result)

        # Ordenar por relevancia
        results.sort(key=lambda x: x['match_score'], reverse=True)

        return Response({
            'results': results,
            'total_results': len(results),
            'search_query': query
        })

    @action(detail=False, methods=['get'], url_path='popular')
    def popular(self, request):
        """Categorías más usadas"""
        limit = int(request.query_params.get('limit', 10))

        from expenses.models import Expense
        categories_usage = {}

        expenses = Expense.objects.filter(
            user=request.user,
            user_expense_category__isnull=False
        )

        for expense in expenses:
            cat_id = expense.user_expense_category.id
            if cat_id not in categories_usage:
                categories_usage[cat_id] = 0
            categories_usage[cat_id] += 1

        # Obtener las más populares
        popular_ids = sorted(categories_usage.keys(),
                             key=lambda x: categories_usage[x],
                             reverse=True)[:limit]

        popular_categories = []
        for cat_id in popular_ids:
            category = self.get_queryset().get(id=cat_id)
            cat_data = self.get_serializer(category).data
            cat_data['usage_count'] = categories_usage[cat_id]
            popular_categories.append(cat_data)

        return Response({
            'categories': popular_categories,
            'total': len(popular_categories)
        })

    @action(detail=False, methods=['get'], url_path='recent')
    def recent(self, request):
        """Categorías usadas recientemente"""
        limit = int(request.query_params.get('limit', 10))

        from expenses.models import Expense
        recent_expenses = Expense.objects.filter(
            user=request.user,
            user_expense_category__isnull=False
        ).order_by('-spent_at')[:50]  # Últimos 50 gastos

        seen_categories = set()
        recent_categories = []

        for expense in recent_expenses:
            cat_id = expense.user_expense_category.id
            if cat_id not in seen_categories and len(recent_categories) < limit:
                seen_categories.add(cat_id)
                cat_data = self.get_serializer(
                    expense.user_expense_category).data
                cat_data['last_used'] = expense.spent_at
                recent_categories.append(cat_data)

        return Response({
            'categories': recent_categories,
            'total': len(recent_categories)
        })

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        """Crear múltiples categorías"""
        categories_data = request.data.get('categories', [])

        if not categories_data:
            return Response(
                {'error': 'Array de categorías es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        created_categories = []
        errors = []

        for cat_data in categories_data:
            try:
                cat_data['user'] = request.user.id
                serializer = self.get_serializer(data=cat_data)
                if serializer.is_valid():
                    category = serializer.save(user=request.user)
                    created_categories.append(serializer.data)
                else:
                    errors.append({
                        'name': cat_data.get('name', 'Sin nombre'),
                        'errors': serializer.errors
                    })
            except Exception as e:
                errors.append({
                    'name': cat_data.get('name', 'Sin nombre'),
                    'errors': str(e)
                })

        return Response({
            'created': created_categories,
            'total_created': len(created_categories),
            'errors': errors,
            'total_errors': len(errors)
        })

    @action(detail=False, methods=['patch'], url_path='bulk-update')
    def bulk_update(self, request):
        """Actualizar múltiples categorías"""
        categories_data = request.data.get('categories', [])

        if not categories_data:
            return Response(
                {'error': 'Array de categorías es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        updated_categories = []
        errors = []

        for cat_data in categories_data:
            try:
                cat_id = cat_data.get('id')
                if not cat_id:
                    errors.append({
                        'name': cat_data.get('name', 'Sin nombre'),
                        'errors': 'ID es requerido para actualización'
                    })
                    continue

                category = self.get_queryset().get(id=cat_id)
                serializer = self.get_serializer(
                    category, data=cat_data, partial=True)
                if serializer.is_valid():
                    serializer.save()
                    updated_categories.append(serializer.data)
                else:
                    errors.append({
                        'id': cat_id,
                        'errors': serializer.errors
                    })
            except UserExpenseCategory.DoesNotExist:
                errors.append({
                    'id': cat_data.get('id'),
                    'errors': 'Categoría no encontrada'
                })
            except Exception as e:
                errors.append({
                    'id': cat_data.get('id'),
                    'errors': str(e)
                })

        return Response({
            'updated': updated_categories,
            'total_updated': len(updated_categories),
            'errors': errors,
            'total_errors': len(errors)
        })

    @action(detail=False, methods=['delete'], url_path='bulk-delete')
    def bulk_delete(self, request):
        """Eliminar múltiples categorías"""
        ids = request.data.get('ids', [])

        if not ids:
            return Response(
                {'error': 'Array de IDs es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        deleted_count = 0
        errors = []

        for cat_id in ids:
            try:
                category = self.get_queryset().get(id=cat_id)
                if category.is_default:
                    errors.append({
                        'id': cat_id,
                        'error': 'No se pueden eliminar categorías predefinidas'
                    })
                else:
                    category.delete()
                    deleted_count += 1
            except UserExpenseCategory.DoesNotExist:
                errors.append({
                    'id': cat_id,
                    'error': 'Categoría no encontrada'
                })
            except Exception as e:
                errors.append({
                    'id': cat_id,
                    'error': str(e)
                })

        return Response({
            'deleted_count': deleted_count,
            'errors': errors,
            'total_errors': len(errors)
        })

    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        """Exportar categorías del usuario"""
        format_type = request.query_params.get('format', 'json')
        categories = self.get_queryset()

        if format_type == 'json':
            data = self.get_serializer(categories, many=True).data
            return Response({
                'categories': data,
                'total': len(data),
                'exported_at': timezone.now(),
                'format': 'json'
            })
        else:
            return Response(
                {'error': 'Formato no soportado. Use: json'},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['post'], url_path='import')
    def import_categories(self, request):
        """Importar categorías"""
        data = request.data.get('data', [])

        if not data:
            return Response(
                {'error': 'Data es requerida para importación'},
                status=status.HTTP_400_BAD_REQUEST
            )

        imported = []
        skipped = []
        errors = []

        for cat_data in data:
            try:
                # Verificar si ya existe
                existing = self.get_queryset().filter(name=cat_data.get('name')).first()
                if existing:
                    skipped.append({
                        'name': cat_data.get('name'),
                        'reason': 'Ya existe'
                    })
                    continue

                serializer = self.get_serializer(data=cat_data)
                if serializer.is_valid():
                    category = serializer.save(user=request.user)
                    imported.append({
                        'name': category.name,
                        'id': category.id,
                        'status': 'created'
                    })
                else:
                    errors.append({
                        'name': cat_data.get('name', 'Sin nombre'),
                        'errors': serializer.errors
                    })
            except Exception as e:
                errors.append({
                    'name': cat_data.get('name', 'Sin nombre'),
                    'errors': str(e)
                })

        return Response({
            'status': 'success',
            'imported': {
                'expense_categories': len(imported)
            },
            'skipped': {
                'duplicates': len(skipped)
            },
            'errors': errors,
            'details': imported
        })

    @action(detail=False, methods=['post'], url_path='reset-to-defaults')
    def reset_to_defaults(self, request):
        """Restaurar categorías predefinidas"""
        # Eliminar categorías personalizadas
        custom_categories = self.get_queryset().filter(is_default=False)
        deleted_count = custom_categories.count()
        custom_categories.delete()

        # Verificar que existan las categorías predefinidas
        predefined_count = self.get_queryset().filter(is_default=True).count()

        return Response({
            'status': 'success',
            'deleted_custom': deleted_count,
            'predefined_available': predefined_count,
            'message': f'Se eliminaron {deleted_count} categorías personalizadas'
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

    @action(detail=False, methods=['get'], url_path='colors-map')
    def colors_map(self, request):
        """Obtener mapa de colores para visualizaciones"""
        categories = self.get_queryset()
        colors_map = {}

        for category in categories:
            colors_map[category.name] = category.color

        if not colors_map:
            # Fallback con colores default
            colors_map = {
                'Sin categoría': '#9E9E9E'
            }

        return Response(colors_map)

    @action(detail=False, methods=['get'], url_path='with-usage')
    def with_usage(self, request):
        """Categorías con estadísticas de uso"""
        days = int(request.query_params.get('days', 30))
        since_date = timezone.now() - timedelta(days=days)

        categories = self.get_queryset()
        categories_with_usage = []

        for category in categories:
            # Obtener estadísticas de uso
            from income.models import Income
            incomes = Income.objects.filter(
                user=request.user,
                user_income_category=category,
                received_at__gte=since_date
            )

            usage_count = incomes.count()
            total_amount = sum(income.amount for income in incomes)
            avg_amount = total_amount / usage_count if usage_count > 0 else 0
            last_income = incomes.order_by('-received_at').first()

            category_data = self.get_serializer(category).data
            category_data['usage_stats'] = {
                'usage_count': usage_count,
                'total_amount': float(total_amount),
                'avg_amount': float(avg_amount),
                'last_used': last_income.received_at if last_income else None,
                'days_since_last_use': (timezone.now().date() - last_income.received_at).days if last_income else None
            }
            categories_with_usage.append(category_data)

        return Response({
            'categories': categories_with_usage,
            'totals': {
                'categories': categories.count(),
                'predefined': categories.filter(is_default=True).count(),
                'custom': categories.filter(is_default=False).count(),
                'total_usage': sum(cat['usage_stats']['usage_count'] for cat in categories_with_usage)
            }
        })

    @action(detail=False, methods=['get'], url_path='search')
    def search(self, request):
        """Buscar categorías por nombre"""
        query = request.query_params.get('q', '').strip()

        if not query:
            return Response(
                {'error': 'Parámetro q es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        categories = self.get_queryset().filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(example__icontains=query)
        )

        results = []
        for category in categories:
            result = self.get_serializer(category).data
            result['type'] = 'income'
            result['match_score'] = 1.0 if query.lower(
            ) in category.name.lower() else 0.8
            results.append(result)

        # Ordenar por relevancia
        results.sort(key=lambda x: x['match_score'], reverse=True)

        return Response({
            'results': results,
            'total_results': len(results),
            'search_query': query
        })

    @action(detail=False, methods=['get'], url_path='popular')
    def popular(self, request):
        """Categorías más usadas"""
        limit = int(request.query_params.get('limit', 10))

        from income.models import Income
        categories_usage = {}

        incomes = Income.objects.filter(
            user=request.user,
            user_income_category__isnull=False
        )

        for income in incomes:
            cat_id = income.user_income_category.id
            if cat_id not in categories_usage:
                categories_usage[cat_id] = 0
            categories_usage[cat_id] += 1

        # Obtener las más populares
        popular_ids = sorted(categories_usage.keys(),
                             key=lambda x: categories_usage[x],
                             reverse=True)[:limit]

        popular_categories = []
        for cat_id in popular_ids:
            category = self.get_queryset().get(id=cat_id)
            cat_data = self.get_serializer(category).data
            cat_data['usage_count'] = categories_usage[cat_id]
            popular_categories.append(cat_data)

        return Response({
            'categories': popular_categories,
            'total': len(popular_categories)
        })

    @action(detail=False, methods=['get'], url_path='recent')
    def recent(self, request):
        """Categorías usadas recientemente"""
        limit = int(request.query_params.get('limit', 10))

        from income.models import Income
        recent_incomes = Income.objects.filter(
            user=request.user,
            user_income_category__isnull=False
        ).order_by('-received_at')[:50]  # Últimos 50 ingresos

        seen_categories = set()
        recent_categories = []

        for income in recent_incomes:
            cat_id = income.user_income_category.id
            if cat_id not in seen_categories and len(recent_categories) < limit:
                seen_categories.add(cat_id)
                cat_data = self.get_serializer(
                    income.user_income_category).data
                cat_data['last_used'] = income.received_at
                recent_categories.append(cat_data)

        return Response({
            'categories': recent_categories,
            'total': len(recent_categories)
        })

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        """Crear múltiples categorías"""
        categories_data = request.data.get('categories', [])

        if not categories_data:
            return Response(
                {'error': 'Array de categorías es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        created_categories = []
        errors = []

        for cat_data in categories_data:
            try:
                cat_data['user'] = request.user.id
                serializer = self.get_serializer(data=cat_data)
                if serializer.is_valid():
                    category = serializer.save(user=request.user)
                    created_categories.append(serializer.data)
                else:
                    errors.append({
                        'name': cat_data.get('name', 'Sin nombre'),
                        'errors': serializer.errors
                    })
            except Exception as e:
                errors.append({
                    'name': cat_data.get('name', 'Sin nombre'),
                    'errors': str(e)
                })

        return Response({
            'created': created_categories,
            'total_created': len(created_categories),
            'errors': errors,
            'total_errors': len(errors)
        })

    @action(detail=False, methods=['patch'], url_path='bulk-update')
    def bulk_update(self, request):
        """Actualizar múltiples categorías"""
        categories_data = request.data.get('categories', [])

        if not categories_data:
            return Response(
                {'error': 'Array de categorías es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        updated_categories = []
        errors = []

        for cat_data in categories_data:
            try:
                cat_id = cat_data.get('id')
                if not cat_id:
                    errors.append({
                        'name': cat_data.get('name', 'Sin nombre'),
                        'errors': 'ID es requerido para actualización'
                    })
                    continue

                category = self.get_queryset().get(id=cat_id)
                serializer = self.get_serializer(
                    category, data=cat_data, partial=True)
                if serializer.is_valid():
                    serializer.save()
                    updated_categories.append(serializer.data)
                else:
                    errors.append({
                        'id': cat_id,
                        'errors': serializer.errors
                    })
            except UserIncomeCategory.DoesNotExist:
                errors.append({
                    'id': cat_data.get('id'),
                    'errors': 'Categoría no encontrada'
                })
            except Exception as e:
                errors.append({
                    'id': cat_data.get('id'),
                    'errors': str(e)
                })

        return Response({
            'updated': updated_categories,
            'total_updated': len(updated_categories),
            'errors': errors,
            'total_errors': len(errors)
        })

    @action(detail=False, methods=['delete'], url_path='bulk-delete')
    def bulk_delete(self, request):
        """Eliminar múltiples categorías"""
        ids = request.data.get('ids', [])

        if not ids:
            return Response(
                {'error': 'Array de IDs es requerido'},
                status=status.HTTP_400_BAD_REQUEST
            )

        deleted_count = 0
        errors = []

        for cat_id in ids:
            try:
                category = self.get_queryset().get(id=cat_id)
                if category.is_default:
                    errors.append({
                        'id': cat_id,
                        'error': 'No se pueden eliminar categorías predefinidas'
                    })
                else:
                    category.delete()
                    deleted_count += 1
            except UserIncomeCategory.DoesNotExist:
                errors.append({
                    'id': cat_id,
                    'error': 'Categoría no encontrada'
                })
            except Exception as e:
                errors.append({
                    'id': cat_id,
                    'error': str(e)
                })

        return Response({
            'deleted_count': deleted_count,
            'errors': errors,
            'total_errors': len(errors)
        })

    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        """Exportar categorías del usuario"""
        format_type = request.query_params.get('format', 'json')
        categories = self.get_queryset()

        if format_type == 'json':
            data = self.get_serializer(categories, many=True).data
            return Response({
                'categories': data,
                'total': len(data),
                'exported_at': timezone.now(),
                'format': 'json'
            })
        else:
            return Response(
                {'error': 'Formato no soportado. Use: json'},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['post'], url_path='import')
    def import_categories(self, request):
        """Importar categorías"""
        data = request.data.get('data', [])

        if not data:
            return Response(
                {'error': 'Data es requerida para importación'},
                status=status.HTTP_400_BAD_REQUEST
            )

        imported = []
        skipped = []
        errors = []

        for cat_data in data:
            try:
                # Verificar si ya existe
                existing = self.get_queryset().filter(name=cat_data.get('name')).first()
                if existing:
                    skipped.append({
                        'name': cat_data.get('name'),
                        'reason': 'Ya existe'
                    })
                    continue

                serializer = self.get_serializer(data=cat_data)
                if serializer.is_valid():
                    category = serializer.save(user=request.user)
                    imported.append({
                        'name': category.name,
                        'id': category.id,
                        'status': 'created'
                    })
                else:
                    errors.append({
                        'name': cat_data.get('name', 'Sin nombre'),
                        'errors': serializer.errors
                    })
            except Exception as e:
                errors.append({
                    'name': cat_data.get('name', 'Sin nombre'),
                    'errors': str(e)
                })

        return Response({
            'status': 'success',
            'imported': {
                'income_categories': len(imported)
            },
            'skipped': {
                'duplicates': len(skipped)
            },
            'errors': errors,
            'details': imported
        })

    @action(detail=False, methods=['post'], url_path='reset-to-defaults')
    def reset_to_defaults(self, request):
        """Restaurar categorías predefinidas"""
        # Eliminar categorías personalizadas
        custom_categories = self.get_queryset().filter(is_default=False)
        deleted_count = custom_categories.count()
        custom_categories.delete()

        # Verificar que existan las categorías predefinidas
        predefined_count = self.get_queryset().filter(is_default=True).count()

        return Response({
            'status': 'success',
            'deleted_custom': deleted_count,
            'predefined_available': predefined_count,
            'message': f'Se eliminaron {deleted_count} categorías personalizadas'
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
