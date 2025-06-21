from rest_framework import serializers
from .models import Income, IncomeCategory
from categories.models import UserIncomeCategory
from categories.serializers import UserIncomeCategorySerializer


class IncomeCategorySerializer(serializers.ModelSerializer):
    """Serializer para categorías globales de ingresos (legacy)"""
    class Meta:
        model = IncomeCategory
        fields = ['id', 'name', 'color', 'description', 'example', 'metadata']
        read_only_fields = ['id']


class IncomeSerializer(serializers.ModelSerializer):
    # Campos legacy para compatibilidad durante la transición
    category_detail = IncomeCategorySerializer(
        source='category', read_only=True)
    category_name = serializers.CharField(write_only=True, required=False)

    # Nuevos campos para categorías por usuario
    user_income_category_detail = UserIncomeCategorySerializer(
        source='user_income_category', read_only=True
    )
    user_category_name = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Income
        fields = [
            'id', 'user', 'amount', 'currency', 'description', 'timestamp',
            'received_at', 'note', 'raw_message', 'created_at', 'updated_at',
            # Campos legacy
            'category', 'category_str', 'category_detail', 'category_name',
            # Nuevos campos por usuario
            'user_income_category', 'user_income_category_detail', 'user_category_name'
        ]
        read_only_fields = [
            'id', 'user', 'created_at', 'updated_at', 'category_detail',
            'user_income_category_detail'
        ]

    def create(self, validated_data):
        """Crear ingreso priorizando categorías por usuario"""
        # El usuario viene del context o se pasa explícitamente
        user = self.context.get(
            'request').user if 'request' in self.context else validated_data.get('user')
        if not user:
            raise serializers.ValidationError("Usuario es requerido")

        # Manejar categoría por usuario (nuevo sistema)
        user_category_name = validated_data.pop('user_category_name', None)
        if user_category_name:
            from categories.utils import get_or_create_user_income_category
            user_category = get_or_create_user_income_category(
                user, user_category_name)
            validated_data['user_income_category'] = user_category[0]

        # Manejar categoría legacy para compatibilidad
        category_name = validated_data.pop('category_name', None)
        if category_name and not user_category_name:
            # Solo usar sistema legacy si no se especificó categoría por usuario
            category, _ = IncomeCategory.objects.get_or_create(
                name=category_name)
            validated_data['category'] = category

            # Crear también la categoría por usuario correspondiente
            from categories.utils import get_or_create_user_income_category
            user_category = get_or_create_user_income_category(
                user, category_name)
            validated_data['user_income_category'] = user_category[0]

        # Asignar usuario
        validated_data['user'] = user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """Actualizar ingreso priorizando categorías por usuario"""
        user = instance.user  # Usar el usuario existente del ingreso

        # Manejar categoría por usuario (nuevo sistema)
        user_category_name = validated_data.pop('user_category_name', None)
        if user_category_name:
            from categories.utils import get_or_create_user_income_category
            user_category = get_or_create_user_income_category(
                user, user_category_name)
            validated_data['user_income_category'] = user_category[0]

        # Manejar categoría legacy para compatibilidad
        category_name = validated_data.pop('category_name', None)
        if category_name and not user_category_name:
            # Solo usar sistema legacy si no se especificó categoría por usuario
            category, _ = IncomeCategory.objects.get_or_create(
                name=category_name)
            validated_data['category'] = category

            # Crear también la categoría por usuario correspondiente
            from categories.utils import get_or_create_user_income_category
            user_category = get_or_create_user_income_category(
                user, category_name)
            validated_data['user_income_category'] = user_category[0]

        return super().update(instance, validated_data)

    def to_representation(self, instance):
        """Personalizar la representación de salida"""
        data = super().to_representation(instance)

        # Priorizar categoría por usuario en la respuesta
        if instance.user_income_category:
            data['current_category'] = {
                'id': instance.user_income_category.id,
                'name': instance.user_income_category.name,
                'color': instance.user_income_category.color,
                'description': instance.user_income_category.description,
                'example': instance.user_income_category.example,
                'is_default': instance.user_income_category.is_default,
                'type': 'user_category'
            }
        elif instance.category:
            data['current_category'] = {
                'id': instance.category.id,
                'name': instance.category.name,
                'color': instance.category.color,
                'description': instance.category.description,
                'example': instance.category.example,
                'type': 'global_category'
            }
        else:
            data['current_category'] = None

        return data
