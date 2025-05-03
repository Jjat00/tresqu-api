from rest_framework import serializers
from .models import Expense
from categories.models import Category


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Expense
        fields = ['id', 'user', 'amount', 'currency', 'description',
                  'timestamp', 'raw_message', 'created_at', 'updated_at',
                  'category', 'category_name', 'category_str', 'spent_at',
                  'note', 'embedding']
        read_only_fields = ['created_at', 'updated_at', 'category']

    def create(self, validated_data):
        category_name = validated_data.pop('category_name', None)
        if category_name:
            category, _ = Category.objects.get_or_create(name=category_name)
            validated_data['category'] = category
        return super().create(validated_data)
