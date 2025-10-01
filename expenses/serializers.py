from rest_framework import serializers
from .models import Expense
from categories.models import Category, UserExpenseCategory
from categories.serializers import UserExpenseCategorySerializer


class ExpenseSerializer(serializers.ModelSerializer):
    # Campos legacy para compatibilidad durante la transición
    category_name = serializers.CharField(write_only=True, required=False)
    category_detail = serializers.SerializerMethodField(read_only=True)

    # Nuevos campos para categorías por usuario
    user_expense_category_detail = UserExpenseCategorySerializer(
        source='user_expense_category', read_only=True
    )
    user_category_name = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Expense
        fields = [
            'id', 'user', 'amount', 'currency', 'description',
            'timestamp', 'spent_at', 'note', 'raw_message',
            'created_at', 'updated_at',
            # Campos legacy
            'category', 'category_name', 'category_str', 'category_detail',
            # Nuevos campos por usuario
            'user_expense_category', 'user_expense_category_detail', 'user_category_name'
        ]
        read_only_fields = [
            'id', 'user', 'created_at', 'updated_at', 'category',
            'category_detail', 'user_expense_category_detail'
        ]

    def get_category_detail(self, obj):
        """Método para obtener detalles de categoría (legacy)"""
        if obj.category:
            return {
                'id': obj.category.id,
                'name': obj.category.name,
                'color': obj.category.color,
                'description': obj.category.description,
                'examples': obj.category.examples
            }
        return None

    def create(self, validated_data):
        """Crear gasto priorizando categorías por usuario"""
        # El usuario viene del context o se pasa explícitamente
        user = self.context.get(
            'request').user if 'request' in self.context else validated_data.get('user')
        if not user:
            raise serializers.ValidationError("Usuario es requerido")

        # Validar límites del plan antes de crear el gasto
        can_add, message = user.can_add_expense()
        if not can_add:
            raise serializers.ValidationError(message)

        # Manejar categoría por usuario (nuevo sistema)
        user_category_name = validated_data.pop('user_category_name', None)
        if user_category_name:
            from categories.utils import get_or_create_user_expense_category
            user_category = get_or_create_user_expense_category(
                user, user_category_name)
            validated_data['user_expense_category'] = user_category[0]

        # Manejar categoría legacy para compatibilidad
        category_name = validated_data.pop('category_name', None)
        if category_name and not user_category_name:
            # Solo usar sistema legacy si no se especificó categoría por usuario
            category, _ = Category.objects.get_or_create(name=category_name)
            validated_data['category'] = category

            # Crear también la categoría por usuario correspondiente
            from categories.utils import get_or_create_user_expense_category
            user_category = get_or_create_user_expense_category(
                user, category_name)
            validated_data['user_expense_category'] = user_category[0]

        # Asignar usuario
        validated_data['user'] = user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """Actualizar gasto priorizando categorías por usuario"""
        user = instance.user  # Usar el usuario existente del gasto

        # Manejar categoría por usuario (nuevo sistema)
        user_category_name = validated_data.pop('user_category_name', None)
        if user_category_name:
            from categories.utils import get_or_create_user_expense_category
            user_category = get_or_create_user_expense_category(
                user, user_category_name)
            validated_data['user_expense_category'] = user_category[0]

        # Manejar categoría legacy para compatibilidad
        category_name = validated_data.pop('category_name', None)
        if category_name and not user_category_name:
            # Solo usar sistema legacy si no se especificó categoría por usuario
            category, _ = Category.objects.get_or_create(name=category_name)
            validated_data['category'] = category

            # Crear también la categoría por usuario correspondiente
            from categories.utils import get_or_create_user_expense_category
            user_category = get_or_create_user_expense_category(
                user, category_name)
            validated_data['user_expense_category'] = user_category[0]

        return super().update(instance, validated_data)

    def to_representation(self, instance):
        """Personalizar la representación de salida"""
        data = super().to_representation(instance)

        # Priorizar categoría por usuario en la respuesta
        if instance.user_expense_category:
            data['current_category'] = {
                'id': instance.user_expense_category.id,
                'name': instance.user_expense_category.name,
                'color': instance.user_expense_category.color,
                'description': instance.user_expense_category.description,
                'examples': instance.user_expense_category.examples,
                'is_default': instance.user_expense_category.is_default,
                'type': 'user_category'
            }
        elif instance.category:
            data['current_category'] = {
                'id': instance.category.id,
                'name': instance.category.name,
                'color': instance.category.color,
                'description': instance.category.description,
                'examples': instance.category.examples,
                'type': 'global_category'
            }
        else:
            data['current_category'] = None

        return data
