from rest_framework import serializers
from .models import Income


class IncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Income
        fields = [
            'id', 'amount', 'currency', 'category', 'category_str',
            'description', 'timestamp', 'received_at', 'note',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
