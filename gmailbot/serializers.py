from rest_framework import serializers

from .models import ProcessedEmail


class ProcessedEmailSerializer(serializers.ModelSerializer):
    """Serializer para emails procesados con detalles del gasto asociado."""

    expense_id = serializers.IntegerField(source='expense.id', allow_null=True, default=None)
    expense_amount = serializers.DecimalField(
        source='expense.amount', max_digits=12, decimal_places=2,
        allow_null=True, default=None,
    )
    expense_currency = serializers.CharField(
        source='expense.currency', allow_null=True, default=None,
    )
    expense_category = serializers.SerializerMethodField()

    class Meta:
        model = ProcessedEmail
        fields = [
            'id', 'gmail_message_id', 'subject', 'sender', 'received_at',
            'is_purchase', 'processing_status', 'ai_response',
            'awaiting_categorization', 'expense_id', 'expense_amount',
            'expense_currency', 'expense_category', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_expense_category(self, obj: ProcessedEmail) -> str | None:
        if obj.expense and obj.expense.user_expense_category:
            return obj.expense.user_expense_category.name
        return None
