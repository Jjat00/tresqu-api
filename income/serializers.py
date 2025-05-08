from rest_framework import serializers
from .models import Income, IncomeCategory


class IncomeCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = IncomeCategory
        fields = ['id', 'name', 'color', 'metadata']
        read_only_fields = ['id']


class IncomeSerializer(serializers.ModelSerializer):
    category_detail = IncomeCategorySerializer(
        source='category', read_only=True)

    class Meta:
        model = Income
        fields = [
            'id', 'amount', 'currency', 'category', 'category_str',
            'category_detail', 'description', 'timestamp', 'received_at', 'note',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at',
                            'updated_at', 'category_detail']
