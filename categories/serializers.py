# /categories/serializers.py
from rest_framework import serializers
from .models import Category, UserExpenseCategory, UserIncomeCategory


class CategorySerializer(serializers.ModelSerializer):
    """Serializer para categorías globales (legacy)"""
    class Meta:
        model = Category
        fields = '__all__'


class UserExpenseCategorySerializer(serializers.ModelSerializer):
    """Serializer para categorías de gastos por usuario"""

    class Meta:
        model = UserExpenseCategory
        fields = [
            'id', 'name', 'color', 'description', 'examples',
            'is_default', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        """Validar que el nombre no esté duplicado para el usuario"""
        user = self.context['request'].user
        if self.instance:
            # En actualización, excluir la instancia actual
            existing = UserExpenseCategory.objects.filter(
                user=user, name=value
            ).exclude(id=self.instance.id)
        else:
            # En creación, buscar duplicados
            existing = UserExpenseCategory.objects.filter(
                user=user, name=value
            )

        if existing.exists():
            raise serializers.ValidationError(
                f"Ya tienes una categoría de gasto llamada '{value}'"
            )
        return value

    def create(self, validated_data):
        """Crear categoría asignando automáticamente el usuario"""
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class UserIncomeCategorySerializer(serializers.ModelSerializer):
    """Serializer para categorías de ingresos por usuario"""

    class Meta:
        model = UserIncomeCategory
        fields = [
            'id', 'name', 'color', 'description', 'example',
            'is_default', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        """Validar que el nombre no esté duplicado para el usuario"""
        user = self.context['request'].user
        if self.instance:
            # En actualización, excluir la instancia actual
            existing = UserIncomeCategory.objects.filter(
                user=user, name=value
            ).exclude(id=self.instance.id)
        else:
            # En creación, buscar duplicados
            existing = UserIncomeCategory.objects.filter(
                user=user, name=value
            )

        if existing.exists():
            raise serializers.ValidationError(
                f"Ya tienes una categoría de ingreso llamada '{value}'"
            )
        return value

    def create(self, validated_data):
        """Crear categoría asignando automáticamente el usuario"""
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class UserCategoriesResponseSerializer(serializers.Serializer):
    """Serializer para respuesta combinada de categorías por usuario"""
    expense_categories = UserExpenseCategorySerializer(
        many=True, read_only=True)
    income_categories = UserIncomeCategorySerializer(many=True, read_only=True)
