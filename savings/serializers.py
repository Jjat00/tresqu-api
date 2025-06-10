from rest_framework import serializers
from decimal import Decimal
from django.utils import timezone
from .models import SavingsCategory, SavingsGoal, SavingsDeposit, SavingsTemplate


class SavingsCategorySerializer(serializers.ModelSerializer):
    """Serializer para categorías de ahorro"""

    class Meta:
        model = SavingsCategory
        fields = ['id', 'name', 'description', 'color', 'icon', 'is_active']


class SavingsDepositSerializer(serializers.ModelSerializer):
    """Serializer para depósitos/retiros de ahorro"""

    is_deposit = serializers.ReadOnlyField()
    is_withdrawal = serializers.ReadOnlyField()

    class Meta:
        model = SavingsDeposit
        fields = [
            'id', 'amount', 'description', 'transaction_type',
            'timestamp', 'source', 'notes', 'is_deposit', 'is_withdrawal'
        ]


class SavingsGoalSerializer(serializers.ModelSerializer):
    """Serializer principal para metas de ahorro"""

    # Campos calculados de solo lectura
    progress_percentage = serializers.ReadOnlyField()
    remaining_amount = serializers.ReadOnlyField()
    is_completed = serializers.ReadOnlyField()
    days_to_target = serializers.ReadOnlyField()
    recommended_daily_saving = serializers.ReadOnlyField()

    # Información de la categoría
    category_name = serializers.CharField(
        source='category.name', read_only=True)
    category_color = serializers.CharField(
        source='category.color', read_only=True)

    # Depósitos recientes (opcional)
    recent_deposits = SavingsDepositSerializer(
        source='deposits',
        many=True,
        read_only=True
    )

    # Validaciones personalizadas
    def validate_target_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "El monto objetivo debe ser mayor a 0")
        return value

    def validate_current_amount(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "El monto actual no puede ser negativo")
        return value

    def validate_target_date(self, value):
        if value and value <= timezone.now().date():
            raise serializers.ValidationError(
                "La fecha objetivo debe ser futura")
        return value

    def validate(self, data):
        # Validar configuración de ahorro automático
        auto_save_enabled = data.get('auto_save_enabled', False)
        auto_save_amount = data.get('auto_save_amount')
        auto_save_frequency = data.get('auto_save_frequency')

        if auto_save_enabled:
            if not auto_save_amount or auto_save_amount <= 0:
                raise serializers.ValidationError({
                    'auto_save_amount': 'Se requiere un monto de ahorro automático válido cuando está habilitado'
                })
            if not auto_save_frequency:
                raise serializers.ValidationError({
                    'auto_save_frequency': 'Se requiere una frecuencia cuando el ahorro automático está habilitado'
                })

        return data

    class Meta:
        model = SavingsGoal
        fields = [
            'id', 'name', 'description', 'category', 'category_name', 'category_color',
            'target_amount', 'current_amount', 'currency', 'target_date',
            'status', 'priority', 'auto_save_enabled', 'auto_save_amount',
            'auto_save_frequency', 'notes', 'created_at', 'completed_at',
            'progress_percentage', 'remaining_amount', 'is_completed',
            'days_to_target', 'recommended_daily_saving', 'recent_deposits'
        ]
        read_only_fields = ['current_amount', 'completed_at']


class SavingsGoalSimpleSerializer(serializers.ModelSerializer):
    """Serializer simplificado para listados y referencias"""

    progress_percentage = serializers.ReadOnlyField()
    is_completed = serializers.ReadOnlyField()
    category_name = serializers.CharField(
        source='category.name', read_only=True)
    category_color = serializers.CharField(
        source='category.color', read_only=True)

    class Meta:
        model = SavingsGoal
        fields = [
            'id', 'name', 'target_amount', 'current_amount', 'currency',
            'status', 'priority', 'category_name', 'category_color',
            'progress_percentage', 'is_completed', 'target_date'
        ]


class SavingsDepositCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear depósitos"""

    def validate_amount(self, value):
        if value == 0:
            raise serializers.ValidationError("El monto no puede ser cero")
        return value

    def validate(self, data):
        # Si es un retiro, validar que hay suficientes fondos
        if data.get('transaction_type') == 'withdrawal' and data.get('amount', 0) > 0:
            # Para retiros, el amount debe ser positivo en el input,
            # se convertirá a negativo en la vista
            savings_goal = data.get('savings_goal')
            if savings_goal and data['amount'] > savings_goal.current_amount:
                raise serializers.ValidationError({
                    'amount': f'No se puede retirar más de ${savings_goal.current_amount} disponibles'
                })

        return data

    class Meta:
        model = SavingsDeposit
        fields = [
            'savings_goal', 'amount', 'description', 'transaction_type',
            'source', 'notes'
        ]


class SavingsTemplateSerializer(serializers.ModelSerializer):
    """Serializer para plantillas de ahorro"""

    category_name = serializers.CharField(
        source='category.name', read_only=True)
    category_color = serializers.CharField(
        source='category.color', read_only=True)

    class Meta:
        model = SavingsTemplate
        fields = [
            'id', 'name', 'description', 'category', 'category_name', 'category_color',
            'suggested_amount_min', 'suggested_amount_max', 'suggested_timeframe_months',
            'priority', 'tips'
        ]


class SavingsGoalCreateFromTemplateSerializer(serializers.Serializer):
    """Serializer para crear una meta de ahorro basada en una plantilla"""

    template_id = serializers.IntegerField()
    name = serializers.CharField(max_length=200)
    target_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    target_date = serializers.DateField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.ChoiceField(
        choices=SavingsGoal.PRIORITY_CHOICES, required=False)
    auto_save_enabled = serializers.BooleanField(required=False, default=False)
    auto_save_amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        allow_null=True
    )
    auto_save_frequency = serializers.ChoiceField(
        choices=[
            ('daily', 'Diario'),
            ('weekly', 'Semanal'),
            ('biweekly', 'Quincenal'),
            ('monthly', 'Mensual'),
        ],
        required=False,
        allow_blank=True
    )

    def validate_template_id(self, value):
        try:
            template = SavingsTemplate.objects.get(id=value, is_active=True)
            self.context['template'] = template
            return value
        except SavingsTemplate.DoesNotExist:
            raise serializers.ValidationError(
                "Plantilla no encontrada o inactiva")

    def validate_target_date(self, value):
        if value and value <= timezone.now().date():
            raise serializers.ValidationError(
                "La fecha objetivo debe ser futura")
        return value


class SavingsSummarySerializer(serializers.Serializer):
    """Serializer para resumen general de ahorros"""

    total_saved = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_target = serializers.DecimalField(max_digits=15, decimal_places=2)
    overall_progress = serializers.FloatField()
    active_goals_count = serializers.IntegerField()
    completed_goals_count = serializers.IntegerField()
    goals_by_priority = serializers.DictField()
    goals_by_status = serializers.DictField()
    monthly_savings_trend = serializers.ListField()
    top_categories = serializers.ListField()


class SavingsAnalyticsSerializer(serializers.Serializer):
    """Serializer para análisis detallado de ahorros"""

    total_deposits = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_withdrawals = serializers.DecimalField(
        max_digits=15, decimal_places=2)
    net_savings = serializers.DecimalField(max_digits=15, decimal_places=2)
    average_monthly_saving = serializers.DecimalField(
        max_digits=15, decimal_places=2)
    savings_velocity = serializers.FloatField()  # Qué tan rápido ahorra
    goal_completion_rate = serializers.FloatField()  # % de metas completadas
    recommended_monthly_saving = serializers.DecimalField(
        max_digits=15, decimal_places=2)
    savings_by_category = serializers.ListField()
    monthly_progress = serializers.ListField()
    upcoming_deadlines = serializers.ListField()
