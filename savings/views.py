from django.shortcuts import render
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, Count, Q, Avg
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
import logging
import calendar
from django.db.models.functions import TruncMonth, TruncWeek, TruncDay

from .models import SavingsCategory, SavingsGoal, SavingsDeposit, SavingsTemplate
from .serializers import (
    SavingsCategorySerializer, SavingsGoalSerializer, SavingsGoalSimpleSerializer,
    SavingsDepositSerializer, SavingsDepositCreateSerializer, SavingsTemplateSerializer,
    SavingsGoalCreateFromTemplateSerializer, SavingsSummarySerializer, SavingsAnalyticsSerializer
)

# Configurar logger
logger = logging.getLogger(__name__)


class SavingsCategoryViewSet(viewsets.ModelViewSet):
    """ViewSet para categorías de ahorro"""

    serializer_class = SavingsCategorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Filtrar categorías por el usuario autenticado"""
        if not self.request.user or not self.request.user.is_authenticated:
            return SavingsCategory.objects.none()
        return SavingsCategory.objects.filter(user=self.request.user, is_active=True)

    def perform_create(self, serializer):
        """Asignar el usuario autenticado al crear una categoría"""
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def with_goals_count(self, request):
        """
        Obtiene categorías con el número de metas activas en cada una
        GET /api/savings/categories/with_goals_count/
        """
        categories = self.get_queryset().annotate(
            active_goals_count=Count('savingsgoal', filter=Q(
                savingsgoal__user=request.user, savingsgoal__status='active'))
        )

        result = []
        for category in categories:
            serializer_data = self.get_serializer(category).data
            serializer_data['active_goals_count'] = category.active_goals_count
            result.append(serializer_data)

        return Response(result)


class SavingsGoalViewSet(viewsets.ModelViewSet):
    """ViewSet para metas de ahorro"""

    serializer_class = SavingsGoalSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Filtrar metas por el usuario autenticado"""
        if not self.request.user or not self.request.user.is_authenticated:
            return SavingsGoal.objects.none()
        return SavingsGoal.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        """Asignar el usuario autenticado al crear una meta"""
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def active(self, request):
        """
        Obtiene solo las metas activas
        GET /api/savings/goals/active/
        """
        queryset = self.get_queryset().filter(status='active')
        serializer = SavingsGoalSimpleSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def completed(self, request):
        """
        Obtiene solo las metas completadas
        GET /api/savings/goals/completed/
        """
        queryset = self.get_queryset().filter(status='completed')
        serializer = SavingsGoalSimpleSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def by_priority(self, request):
        """
        Obtiene metas agrupadas por prioridad
        GET /api/savings/goals/by_priority/
        """
        priorities = ['urgent', 'high', 'medium', 'low']
        result = {}

        for priority in priorities:
            goals = self.get_queryset().filter(priority=priority, status='active')
            serializer = SavingsGoalSimpleSerializer(goals, many=True)
            result[priority] = serializer.data

        return Response(result)

    @action(detail=True, methods=['post'])
    def add_deposit(self, request, pk=None):
        """
        Agrega un depósito a una meta específica
        POST /api/savings/goals/{id}/add_deposit/
        {
            "amount": 100.00,
            "description": "Depósito semanal",
            "transaction_type": "manual",
            "source": "Salario"
        }
        """
        goal = self.get_object()

        serializer = SavingsDepositCreateSerializer(data=request.data)
        if serializer.is_valid():
            amount = serializer.validated_data['amount']
            description = serializer.validated_data.get('description', '')
            transaction_type = serializer.validated_data.get(
                'transaction_type', 'manual')
            source = serializer.validated_data.get('source', '')
            notes = serializer.validated_data.get('notes', '')

            try:
                deposit = goal.add_deposit(
                    amount=amount,
                    description=description,
                    transaction_type=transaction_type
                )
                deposit.source = source
                deposit.notes = notes
                deposit.save()

                # Devolver información actualizada de la meta
                goal.refresh_from_db()
                goal_serializer = self.get_serializer(goal)

                return Response({
                    'message': 'Depósito agregado exitosamente',
                    'deposit': SavingsDepositSerializer(deposit).data,
                    'updated_goal': goal_serializer.data
                }, status=status.HTTP_201_CREATED)

            except Exception as e:
                return Response({
                    'error': f'Error al agregar depósito: {str(e)}'
                }, status=status.HTTP_400_BAD_REQUEST)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def withdraw(self, request, pk=None):
        """
        Retira dinero de una meta específica
        POST /api/savings/goals/{id}/withdraw/
        {
            "amount": 50.00,
            "description": "Retiro para emergencia"
        }
        """
        goal = self.get_object()

        amount = request.data.get('amount')
        description = request.data.get('description', 'Retiro')

        if not amount or amount <= 0:
            return Response({
                'error': 'El monto debe ser mayor a 0'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            withdrawal = goal.withdraw(amount=Decimal(
                str(amount)), description=description)

            # Devolver información actualizada de la meta
            goal.refresh_from_db()
            goal_serializer = self.get_serializer(goal)

            return Response({
                'message': 'Retiro realizado exitosamente',
                'withdrawal': SavingsDepositSerializer(withdrawal).data,
                'updated_goal': goal_serializer.data
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                'error': f'Error al realizar retiro: {str(e)}'
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def progress_chart(self, request, pk=None):
        """
        Obtiene datos para gráfico de progreso temporal de una meta
        GET /api/savings/goals/{id}/progress_chart/
        """
        goal = self.get_object()

        # Obtener todos los depósitos de esta meta
        deposits = goal.deposits.order_by('timestamp')

        if not deposits.exists():
            return Response({
                'labels': [],
                'data': [],
                'target_line': []
            })

        # Preparar datos para el gráfico
        labels = []
        cumulative_amounts = []
        running_total = Decimal('0.00')

        for deposit in deposits:
            labels.append(deposit.timestamp.strftime('%d/%m/%Y'))
            running_total += deposit.amount
            cumulative_amounts.append(float(running_total))

        # Línea de meta (constante)
        target_line = [float(goal.target_amount)] * len(labels)

        return Response({
            'labels': labels,
            'data': cumulative_amounts,
            'target_line': target_line,
            'goal_info': {
                'name': goal.name,
                'target_amount': float(goal.target_amount),
                'current_amount': float(goal.current_amount),
                'progress_percentage': goal.progress_percentage
            }
        })

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        Obtiene un resumen completo de todas las metas de ahorro
        GET /api/savings/goals/summary/
        """
        queryset = self.get_queryset()

        # Estadísticas generales
        total_saved = queryset.aggregate(total=Sum('current_amount'))[
            'total'] or Decimal('0')
        total_target = queryset.aggregate(total=Sum('target_amount'))[
            'total'] or Decimal('0')

        # Conteos por estado
        active_count = queryset.filter(status='active').count()
        completed_count = queryset.filter(status='completed').count()
        paused_count = queryset.filter(status='paused').count()

        # Progreso general
        overall_progress = 0
        if total_target > 0:
            overall_progress = float((total_saved / total_target) * 100)

        # Agrupación por prioridad
        goals_by_priority = {}
        for priority, label in SavingsGoal.PRIORITY_CHOICES:
            count = queryset.filter(priority=priority, status='active').count()
            goals_by_priority[priority] = {
                'label': label,
                'count': count
            }

        # Top categorías
        top_categories = []
        if queryset.exists():
            category_stats = queryset.values(
                'category__name', 'category__color'
            ).annotate(
                total_saved=Sum('current_amount'),
                goals_count=Count('id')
            ).order_by('-total_saved')[:5]

            for cat in category_stats:
                top_categories.append({
                    'name': cat['category__name'] or 'Sin categoría',
                    'color': cat['category__color'] or '#CCCCCC',
                    'total_saved': float(cat['total_saved'] or 0),
                    'goals_count': cat['goals_count']
                })

        # Metas próximas a vencer
        upcoming_deadlines = queryset.filter(
            status='active',
            target_date__isnull=False,
            target_date__lte=timezone.now().date() + timedelta(days=30)
        ).order_by('target_date')[:5]

        upcoming_deadlines_data = SavingsGoalSimpleSerializer(
            upcoming_deadlines, many=True).data

        return Response({
            'total_saved': float(total_saved),
            'total_target': float(total_target),
            'overall_progress': round(overall_progress, 2),
            'active_goals_count': active_count,
            'completed_goals_count': completed_count,
            'paused_goals_count': paused_count,
            'goals_by_priority': goals_by_priority,
            'top_categories': top_categories,
            'upcoming_deadlines': upcoming_deadlines_data
        })

    @action(detail=False, methods=['get'])
    def analytics(self, request):
        """
        Obtiene análisis avanzado de ahorros
        GET /api/savings/goals/analytics/
        """
        queryset = self.get_queryset()

        # Análisis de depósitos y retiros
        all_deposits = SavingsDeposit.objects.filter(
            savings_goal__user=request.user)

        total_deposits = all_deposits.filter(amount__gt=0).aggregate(
            total=Sum('amount'))['total'] or Decimal('0')
        total_withdrawals = abs(all_deposits.filter(amount__lt=0).aggregate(
            total=Sum('amount'))['total'] or Decimal('0'))
        net_savings = total_deposits - total_withdrawals

        # Promedio mensual de ahorro (últimos 12 meses)
        twelve_months_ago = timezone.now() - timedelta(days=365)
        recent_deposits = all_deposits.filter(
            timestamp__gte=twelve_months_ago,
            amount__gt=0
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        average_monthly_saving = recent_deposits / 12

        # Velocidad de ahorro (% de metas completadas vs tiempo promedio)
        completed_goals = queryset.filter(
            status='completed', completed_at__isnull=False)
        total_goals = queryset.count()
        goal_completion_rate = 0
        if total_goals > 0:
            goal_completion_rate = (
                completed_goals.count() / total_goals) * 100

        # Ahorro recomendado mensual basado en metas activas
        active_goals = queryset.filter(status='active')
        recommended_monthly_saving = Decimal('0')

        for goal in active_goals:
            if goal.days_to_target and goal.days_to_target > 0:
                monthly_needed = (goal.remaining_amount *
                                  30) / goal.days_to_target
                recommended_monthly_saving += monthly_needed

        # Ahorros por categoría
        savings_by_category = []
        category_stats = queryset.values(
            'category__name', 'category__color'
        ).annotate(
            total_saved=Sum('current_amount'),
            total_target=Sum('target_amount')
        ).order_by('-total_saved')

        for cat in category_stats:
            savings_by_category.append({
                'category': cat['category__name'] or 'Sin categoría',
                'color': cat['category__color'] or '#CCCCCC',
                'saved': float(cat['total_saved'] or 0),
                'target': float(cat['total_target'] or 0),
                'progress': round((cat['total_saved'] / cat['total_target'] * 100), 2) if cat['total_target'] > 0 else 0
            })

        # Progreso mensual (últimos 6 meses)
        monthly_progress = []
        for i in range(6):
            month_start = (timezone.now().replace(day=1) -
                           timedelta(days=30*i)).replace(day=1)
            if i == 0:
                month_end = timezone.now()
            else:
                next_month = month_start.replace(
                    month=month_start.month + 1) if month_start.month < 12 else month_start.replace(year=month_start.year + 1, month=1)
                month_end = next_month - timedelta(days=1)

            month_deposits = all_deposits.filter(
                timestamp__gte=month_start,
                timestamp__lte=month_end,
                amount__gt=0
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            monthly_progress.insert(0, {
                'month': month_start.strftime('%b %Y'),
                'amount': float(month_deposits)
            })

        return Response({
            'total_deposits': float(total_deposits),
            'total_withdrawals': float(total_withdrawals),
            'net_savings': float(net_savings),
            'average_monthly_saving': float(average_monthly_saving),
            'goal_completion_rate': round(goal_completion_rate, 2),
            'recommended_monthly_saving': float(recommended_monthly_saving),
            'savings_by_category': savings_by_category,
            'monthly_progress': monthly_progress
        })

    @action(detail=False, methods=['get'])
    def recommendations(self, request):
        """
        Obtiene recomendaciones personalizadas de ahorro
        GET /api/savings/goals/recommendations/
        """
        # Importar modelos de expenses e income para análisis
        try:
            from expenses.models import Expense
            from income.models import Income

            # Calcular ingresos y gastos del último mes
            last_month = timezone.now() - timedelta(days=30)

            monthly_income = Income.objects.filter(
                user=request.user,
                timestamp__gte=last_month
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            monthly_expenses = Expense.objects.filter(
                user=request.user,
                timestamp__gte=last_month
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            available_for_savings = monthly_income - monthly_expenses

        except ImportError:
            available_for_savings = Decimal('0')
            monthly_income = Decimal('0')
            monthly_expenses = Decimal('0')

        recommendations = []

        # Recomendación 1: Fondo de emergencia
        emergency_fund = self.get_queryset().filter(
            category__name__icontains='emergencia'
        ).first()

        if not emergency_fund and monthly_expenses > 0:
            emergency_target = monthly_expenses * 6  # 6 meses de gastos
            recommendations.append({
                'type': 'emergency_fund',
                'title': 'Crear Fondo de Emergencia',
                'description': f'Se recomienda tener un fondo de emergencia equivalente a 6 meses de gastos (${emergency_target:,.2f})',
                'suggested_amount': float(emergency_target),
                'priority': 'urgent',
                'category': 'Fondo de Emergencia'
            })

        # Recomendación 2: Porcentaje de ahorro
        if available_for_savings > 0:
            current_savings_rate = 0
            total_monthly_savings = SavingsDeposit.objects.filter(
                savings_goal__user=request.user,
                timestamp__gte=last_month,
                amount__gt=0
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            if monthly_income > 0:
                current_savings_rate = (
                    total_monthly_savings / monthly_income) * 100

            if current_savings_rate < 20:  # Recomendación estándar del 20%
                recommended_savings = monthly_income * Decimal('0.20')
                recommendations.append({
                    'type': 'savings_rate',
                    'title': 'Aumentar Tasa de Ahorro',
                    'description': f'Tu tasa de ahorro actual es {current_savings_rate:.1f}%. Se recomienda ahorrar al menos el 20% de tus ingresos (${recommended_savings:,.2f} mensual)',
                    'current_rate': float(current_savings_rate),
                    'recommended_rate': 20.0,
                    'suggested_monthly_amount': float(recommended_savings)
                })

        # Recomendación 3: Metas sin progreso reciente
        stalled_goals = self.get_queryset().filter(
            status='active'
        ).exclude(
            deposits__timestamp__gte=timezone.now() - timedelta(days=30)
        )

        if stalled_goals.exists():
            recommendations.append({
                'type': 'stalled_goals',
                'title': 'Metas sin Progreso Reciente',
                'description': f'Tienes {stalled_goals.count()} meta(s) sin depósitos en el último mes',
                'stalled_goals': SavingsGoalSimpleSerializer(stalled_goals, many=True).data
            })

        # Recomendación 4: Metas próximas a vencer
        urgent_goals = self.get_queryset().filter(
            status='active',
            target_date__isnull=False,
            target_date__lte=timezone.now().date() + timedelta(days=60)
        )

        for goal in urgent_goals:
            if goal.days_to_target and goal.days_to_target > 0:
                daily_needed = goal.recommended_daily_saving
                recommendations.append({
                    'type': 'urgent_goal',
                    'title': f'Meta Próxima: {goal.name}',
                    'description': f'Necesitas ahorrar ${daily_needed:,.2f} diarios para alcanzar esta meta a tiempo',
                    'goal': SavingsGoalSimpleSerializer(goal).data,
                    'daily_amount_needed': float(daily_needed),
                    'days_remaining': goal.days_to_target
                })

        return Response({
            'available_for_savings': float(available_for_savings),
            'monthly_income': float(monthly_income),
            'monthly_expenses': float(monthly_expenses),
            'recommendations': recommendations
        })


class SavingsDepositViewSet(viewsets.ModelViewSet):
    """ViewSet para depósitos de ahorro"""

    serializer_class = SavingsDepositSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Filtrar depósitos por el usuario autenticado"""
        if not self.request.user or not self.request.user.is_authenticated:
            return SavingsDeposit.objects.none()
        return SavingsDeposit.objects.filter(savings_goal__user=self.request.user)

    @action(detail=False, methods=['get'])
    def recent(self, request):
        """
        Obtiene los depósitos más recientes
        GET /api/savings/deposits/recent/?limit=20
        """
        limit = int(request.query_params.get('limit', 20))
        queryset = self.get_queryset().order_by('-timestamp')[:limit]
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def by_goal(self, request):
        """
        Obtiene depósitos agrupados por meta de ahorro
        GET /api/savings/deposits/by_goal/?goal_id=uuid
        """
        goal_id = request.query_params.get('goal_id')
        if goal_id:
            queryset = self.get_queryset().filter(savings_goal_id=goal_id)
        else:
            queryset = self.get_queryset()

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class SavingsTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet para plantillas de ahorro (solo lectura)"""

    queryset = SavingsTemplate.objects.filter(is_active=True)
    serializer_class = SavingsTemplateSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """
        Obtiene plantillas agrupadas por categoría del usuario
        GET /api/savings/templates/by_category/
        """
        # Solo mostrar plantillas de categorías que pertenecen al usuario actual
        user_categories = SavingsCategory.objects.filter(
            user=request.user, is_active=True)
        result = {}

        for category in user_categories:
            templates = self.get_queryset().filter(category=category)
            serializer = self.get_serializer(templates, many=True)
            result[category.name] = {
                'category_info': SavingsCategorySerializer(category).data,
                'templates': serializer.data
            }

        return Response(result)

    @action(detail=True, methods=['post'])
    def create_goal_from_template(self, request, pk=None):
        """
        Crea una meta de ahorro basada en una plantilla
        POST /api/savings/templates/{id}/create_goal_from_template/
        """
        template = self.get_object()

        serializer = SavingsGoalCreateFromTemplateSerializer(
            data=request.data,
            context={'template': template}
        )

        if serializer.is_valid():
            # Crear la meta basada en la plantilla
            goal_data = {
                'user': request.user,
                'name': serializer.validated_data['name'],
                'description': serializer.validated_data.get('description', template.description),
                'category': template.category,
                'target_amount': serializer.validated_data['target_amount'],
                'target_date': serializer.validated_data.get('target_date'),
                'priority': serializer.validated_data.get('priority', template.priority),
                'auto_save_enabled': serializer.validated_data.get('auto_save_enabled', False),
                'auto_save_amount': serializer.validated_data.get('auto_save_amount'),
                'auto_save_frequency': serializer.validated_data.get('auto_save_frequency'),
            }

            goal = SavingsGoal.objects.create(**goal_data)
            goal_serializer = SavingsGoalSerializer(goal)

            return Response({
                'message': 'Meta creada exitosamente desde plantilla',
                'goal': goal_serializer.data,
                'template_used': self.get_serializer(template).data
            }, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
